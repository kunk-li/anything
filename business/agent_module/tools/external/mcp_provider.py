# -*- coding: utf-8 -*-
"""
MCP 客户端 (外部工具连接 RFC · Stage2 + 增量).

参考 Codex / Claude Code 的做法: 实现**标准 MCP (Model Context Protocol)** 客户端 ——
JSON-RPC 2.0, 两种传输:
  - **stdio** (本地子进程 server, newline-delimited): `McpStdioClient`
  - **HTTP** (远程 server, Streamable HTTP: POST JSON-RPC, 响应 json 或 SSE, Mcp-Session-Id): `McpHttpClient`
**最小自实现** (无 `mcp` SDK 依赖, 契合本项目少依赖); 接 Stage1 的 `ExternalToolProvider` 抽象。

协议流程: initialize → notifications/initialized → tools/list → tools/call。
工具命名空间 `server.tool`。仅连**显式配置**的 server。transport/post 可注入 (测试不起真子进程/不发网络)。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .base import ExternalToolDef, ExternalToolProvider
from .http_provider import _url_ssrf_error

PROTOCOL_VERSION = "2024-11-05"   # MCP 协议版本 (server 一般宽容; 需要时可调)
_MAX_READ_LINES = 2000            # stdio 单次 request 读响应的行数上限 (防卡死)
_CLIENT_INFO = {"name": "anything-agent", "version": "1.0"}


@dataclass
class McpServerSpec:
    """声明一个 MCP server (stdio 或 http)。"""
    name: str                                      # server 标识 (工具名前缀)
    transport: str = "stdio"                       # stdio | http
    # --- stdio ---
    command: str = ""                              # stdio 启动命令 (如 "npx" / "python")
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    # --- http ---
    url: str = ""                                  # http server 端点
    headers: Dict[str, str] = field(default_factory=dict)
    auth_header: str = ""
    auth_value: str = ""
    # --- 通用 ---
    enabled: bool = True
    timeout: int = 30
    requires_approval: bool = True


# ============================================================
# 客户端: 传输无关的 MCP 动作逻辑 + 两种传输实现
# ============================================================

class _McpClientBase:
    """MCP 动作逻辑 (initialize/list_tools/call_tool), 传输无关。
    子类实现 `_rpc(method, params) -> result` (出错抛) 与 `_notify(method, params)`。"""

    def __init__(self):
        self._id = 0
        self._lock = threading.Lock()
        self._initialized = False

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        raise NotImplementedError

    def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        raise NotImplementedError

    def initialize(self) -> None:
        if self._initialized:
            return
        self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": _CLIENT_INFO,
        })
        self._notify("notifications/initialized")
        self._initialized = True

    def list_tools(self) -> List[Dict[str, Any]]:
        self.initialize()
        result = self._rpc("tools/list") or {}
        tools = result.get("tools") if isinstance(result, dict) else None
        return tools or []

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        self.initialize()
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})

    def close(self) -> None:
        pass


class McpStdioClient(_McpClientBase):
    """stdio JSON-RPC MCP 客户端 (一个 server 一个 client; newline-delimited)。
    `transport` 可注入 (须有 `write(str)`/`readline()->str`); 默认 None → 真 subprocess (lazy 启动)。"""

    def __init__(self, spec: McpServerSpec, transport: Any = None):
        super().__init__()
        self.spec = spec
        self._transport = transport
        self._proc: Optional[subprocess.Popen] = None

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

    def _rpc(self, method, params=None):
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
                if msg.get("id") == rid:
                    if "error" in msg:
                        raise RuntimeError(f"MCP error: {msg['error']}")
                    return msg.get("result")
            raise RuntimeError("MCP 响应超过最大读取行数")

    def _notify(self, method, params=None):
        self._ensure_started()
        self._write(json.dumps({"jsonrpc": "2.0", "method": method,
                                "params": params or {}}, ensure_ascii=False) + "\n")

    def close(self) -> None:
        try:
            if self._proc is not None:
                self._proc.terminate()
        except Exception:
            pass


def _default_http_post(url: str, payload: Dict[str, Any], headers: Dict[str, str],
                       timeout: int = 30):
    """SSRF 安全的 HTTP POST (stdlib urllib)。返回 (status:int, resp_headers:dict, body:str)。"""
    err = _url_ssrf_error(url)
    if err:
        raise RuntimeError(err)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
        return getattr(resp, "status", 200), {k: v for k, v in resp.headers.items()}, raw


def _parse_mcp_http_body(body: str, rid: int) -> Optional[Dict[str, Any]]:
    """从 HTTP 响应体取匹配 id 的 JSON-RPC 消息。支持 SSE(data: 行) 与 纯 JSON(对象/数组)。"""
    body = (body or "").strip()
    if not body:
        return None
    if "data:" in body:                      # SSE 帧
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == rid:
                    return msg
        return None
    try:
        obj = json.loads(body)
    except ValueError:
        return None
    if isinstance(obj, list):
        for m in obj:
            if isinstance(m, dict) and m.get("id") == rid:
                return m
        return None
    return obj if isinstance(obj, dict) else None


class McpHttpClient(_McpClientBase):
    """HTTP (Streamable HTTP) JSON-RPC MCP 客户端: POST 请求, 解析 json/sse 响应,
    捕获并复用 `Mcp-Session-Id`。`post(url, payload, headers)->(status,headers,body)` 可注入(测试)。"""

    def __init__(self, spec: McpServerSpec, post: Optional[Callable] = None):
        super().__init__()
        self.spec = spec
        self._post = post or _default_http_post
        self._session_id: Optional[str] = None

    def _req_headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream",
             **dict(self.spec.headers)}
        if self.spec.auth_header and self.spec.auth_value:
            h[self.spec.auth_header] = self.spec.auth_value
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _rpc(self, method, params=None):
        with self._lock:
            self._id += 1
            rid = self._id
            payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
            status, resp_headers, body = self._post(self.spec.url, payload, self._req_headers())
            sid = (resp_headers or {}).get("Mcp-Session-Id") or (resp_headers or {}).get("mcp-session-id")
            if sid:
                self._session_id = sid
            msg = _parse_mcp_http_body(body, rid)
            if msg is None:
                raise RuntimeError(f"MCP HTTP 无匹配响应 (status={status})")
            if "error" in msg:
                raise RuntimeError(f"MCP error: {msg['error']}")
            return msg.get("result")

    def _notify(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        try:
            self._post(self.spec.url, payload, self._req_headers())   # 通知 best-effort
        except Exception:
            pass


def _default_client_for(spec: McpServerSpec) -> _McpClientBase:
    return McpHttpClient(spec) if spec.transport == "http" else McpStdioClient(spec)


# ============================================================
# Provider
# ============================================================

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
    """连接配置的 MCP server (stdio/http), 发现其 tools 并包成 agent 工具 (命名空间 server.tool)。
    client_factory 可注入 (测试用); 默认按 spec.transport 选 stdio/http 客户端。"""

    def __init__(self, specs: List[McpServerSpec],
                 client_factory: Optional[Callable[[McpServerSpec], _McpClientBase]] = None):
        self.specs = [s for s in specs if s.enabled]
        self._client_factory = client_factory or _default_client_for
        self._clients: List[_McpClientBase] = []   # 生命周期管理: 持有已建连接, 供 close() 释放

    def discover(self) -> List[ExternalToolDef]:
        out: List[ExternalToolDef] = []
        for spec in self.specs:
            try:
                client = self._client_factory(spec)
                tools = client.list_tools()
            except Exception:
                continue  # fail-safe: server 连不上 → 跳过, 不拖垮启动
            self._clients.append(client)   # 连上的 client 留引用, 退出时可 close (stdio 杀子进程)
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
    def _make_tool(client: _McpClientBase, remote_name: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        def tool(payload: Dict[str, Any]) -> Dict[str, Any]:
            try:
                result = client.call_tool(remote_name, payload or {})
            except Exception as e:
                return {"code": "TOOL_CALL_FAILED", "message": f"MCP 调用失败: {e}",
                        "data": None, "retryable": True}
            return _mcp_result_to_envelope(result)
        return tool

    def close(self) -> None:
        """生命周期管理: 关闭所有已建连接 (stdio 终止子进程, http 释放会话)。
        逐个 fail-safe (单个关闭失败不影响其余); 幂等 (关后清空引用)。"""
        for c in self._clients:
            try:
                c.close()
            except Exception:
                pass
        self._clients = []
