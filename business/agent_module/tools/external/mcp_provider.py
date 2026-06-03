# -*- coding: utf-8 -*-
"""
MCP 客户端 (外部工具连接 RFC · Stage2).

参考 Codex / Claude Code 的做法: 实现**标准 MCP (Model Context Protocol)** 客户端 ——
JSON-RPC 2.0 over **stdio** (newline-delimited), 连接外部 MCP server → 发现并注册其 tools。
**最小自实现** (无 `mcp` SDK 依赖, 契合本项目少依赖哲学); 接 Stage1 的 `ExternalToolProvider` 抽象。

协议流程 (与标准 MCP server 互通):
    initialize → notifications/initialized → tools/list → tools/call
工具命名空间 `server.tool` 避免多 server 冲突。stdio server = 子进程, 仅连**显式配置**的 server。
transport 可注入 (测试用 fake, 不起真子进程)。fail-safe: server 连不上跳过, 不拖垮启动。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .base import ExternalToolDef, ExternalToolProvider

PROTOCOL_VERSION = "2024-11-05"   # MCP 协议版本 (server 一般宽容; 需要时可调)
_MAX_READ_LINES = 2000            # 单次 request 读响应的行数上限 (防卡死)


@dataclass
class McpServerSpec:
    """声明一个 stdio MCP server。"""
    name: str                                       # server 标识 (工具名前缀)
    command: str                                    # 启动命令 (如 "npx" / "python")
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout: int = 30
    requires_approval: bool = True


class McpStdioClient:
    """最小 stdio JSON-RPC 2.0 MCP 客户端 (一个 server 一个 client)。

    newline-delimited JSON-RPC; 同步阻塞 (send → 读到匹配 id 的响应, 跳过通知/不匹配)。
    `transport` 可注入 (须有 `write(str)` / `readline()->str`); 默认 None → 真 subprocess (lazy 启动)。
    """

    def __init__(self, spec: McpServerSpec, transport: Any = None):
        self.spec = spec
        self._transport = transport
        self._proc: Optional[subprocess.Popen] = None
        self._id = 0
        self._lock = threading.Lock()
        self._initialized = False

    # ---- 传输 ----
    def _ensure_started(self) -> None:
        if self._transport is not None or self._proc is not None:
            return
        env = {**os.environ, **(self.spec.env or {})}
        self._proc = subprocess.Popen(
            [self.spec.command, *self.spec.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", env=env, bufsize=1,
        )

    def _write(self, line: str) -> None:
        if self._transport is not None:
            self._transport.write(line)
        else:
            self._proc.stdin.write(line)          # type: ignore[union-attr]
            self._proc.stdin.flush()              # type: ignore[union-attr]

    def _readline(self) -> str:
        if self._transport is not None:
            return self._transport.readline()
        return self._proc.stdout.readline()       # type: ignore[union-attr]

    # ---- JSON-RPC ----
    def _request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self._ensure_started()
        with self._lock:
            self._id += 1
            rid = self._id
            self._write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                                    "params": params or {}}, ensure_ascii=False) + "\n")
            for _ in range(_MAX_READ_LINES):
                line = self._readline()
                if not line:
                    raise RuntimeError("MCP server 无响应/已关闭")
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == rid:           # 跳过通知 / 不匹配 id
                    if "error" in msg:
                        raise RuntimeError(f"MCP error: {msg['error']}")
                    return msg.get("result")
            raise RuntimeError("MCP 响应超过最大读取行数")

    def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        self._ensure_started()
        self._write(json.dumps({"jsonrpc": "2.0", "method": method,
                                "params": params or {}}, ensure_ascii=False) + "\n")

    # ---- MCP 动作 ----
    def initialize(self) -> None:
        if self._initialized:
            return
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "anything-agent", "version": "1.0"},
        })
        self._notify("notifications/initialized")
        self._initialized = True

    def list_tools(self) -> List[Dict[str, Any]]:
        self.initialize()
        result = self._request("tools/list") or {}
        tools = result.get("tools") if isinstance(result, dict) else None
        return tools or []

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        self.initialize()
        return self._request("tools/call", {"name": name, "arguments": arguments or {}})

    def close(self) -> None:
        try:
            if self._proc is not None:
                self._proc.terminate()
        except Exception:
            pass


def _mcp_result_to_envelope(result: Any) -> Dict[str, Any]:
    """MCP tools/call 结果 → 标准响应 envelope。result: {content:[{type,text}], isError}。"""
    is_error = bool(isinstance(result, dict) and result.get("isError"))
    content = result.get("content") if isinstance(result, dict) else None
    text = ""
    if isinstance(content, list):
        text = "\n".join(c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text")
    return {
        "code": "TOOL_CALL_FAILED" if is_error else "SUCCESS",
        "message": "MCP 工具返回错误" if is_error else "ok",
        "data": {"result": text if text else result},
        "retryable": False,
    }


class McpToolProvider(ExternalToolProvider):
    """连接配置的 MCP server, 发现其 tools 并包成 agent 工具 (命名空间 server.tool)。

    client_factory 可注入 (测试用); 默认 McpStdioClient(真 subprocess)。
    """

    def __init__(self, specs: List[McpServerSpec],
                 client_factory: Optional[Callable[[McpServerSpec], McpStdioClient]] = None):
        self.specs = [s for s in specs if s.enabled]
        self._client_factory = client_factory or (lambda spec: McpStdioClient(spec))

    def discover(self) -> List[ExternalToolDef]:
        out: List[ExternalToolDef] = []
        for spec in self.specs:
            try:
                client = self._client_factory(spec)
                tools = client.list_tools()
            except Exception:
                continue  # fail-safe: server 连不上 → 跳过, 不拖垮启动
            for t in tools:
                rname = t.get("name") if isinstance(t, dict) else None
                if not rname:
                    continue
                out.append(ExternalToolDef(
                    name=f"{spec.name}.{rname}",          # 命名空间避冲突
                    func=self._make_tool(client, rname),
                    description=(t.get("description") or f"MCP {spec.name}: {rname}"),
                    input_schema=t.get("inputSchema") or {},
                    requires_approval=spec.requires_approval,
                    source=f"mcp:{spec.name}",
                ))
        return out

    @staticmethod
    def _make_tool(client: McpStdioClient, remote_name: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        def tool(payload: Dict[str, Any]) -> Dict[str, Any]:
            try:
                result = client.call_tool(remote_name, payload or {})
            except Exception as e:
                return {"code": "TOOL_CALL_FAILED", "message": f"MCP 调用失败: {e}",
                        "data": None, "retryable": True}
            return _mcp_result_to_envelope(result)
        return tool
