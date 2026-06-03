# -*- coding: utf-8 -*-
"""
HTTP/OpenAPI 外部工具连接器 (外部工具连接 RFC · Stage1).

配置声明一个外部 REST API → 自动生成一个 **SSRF 安全** 的 agent 工具 callable。
复用 `tools_impl/http_get` 的私网防御 (拒 SSRF) + 超时/大小限制。零新依赖 (stdlib urllib)。
fetch 可注入 (测试用), 默认走 SSRF 安全 urllib (禁跳转)。
"""
from __future__ import annotations

import ipaddress
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .base import ExternalToolDef, ExternalToolProvider
# 复用 http_get 的 SSRF 防御 (同包内部复用, 不重复造安全逻辑)
from ..tools_impl.http_get import _is_private_ip, _resolve_safe


@dataclass
class HttpToolSpec:
    """声明一个外部 HTTP 工具。"""
    name: str
    url: str                                          # 可含 {占位}, 从 payload 填
    method: str = "GET"
    description: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    query: List[str] = field(default_factory=list)    # 从 payload 取作 query string 的键
    body: List[str] = field(default_factory=list)     # 从 payload 取作 JSON body 的键 (POST/PUT/PATCH)
    auth_header: str = ""                             # 如 "Authorization"
    auth_value: str = ""                              # 注入 auth_header 的凭据 (建议来自 env/加密 secret, 勿明文入库)
    timeout: int = 10
    max_bytes: int = 1048576
    response_path: str = ""                           # 点路径从 JSON 响应抽取 (如 "data.items"), 空=整个解析结果
    requires_approval: bool = True


def _url_ssrf_error(url: str) -> Optional[str]:
    """复用 http_get 的 SSRF 校验: 仅 http/https + host 非私网。安全→None, 否则→错误信息。"""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as e:
        return f"url 解析失败: {e}"
    if parsed.scheme not in ("http", "https"):
        return "仅允许 http/https 协议"
    host = parsed.hostname or ""
    if not host:
        return "url 缺 host"
    try:
        ip = ipaddress.ip_address(host)
        return f"拒绝私网 IP (SSRF 防御): {host}" if _is_private_ip(str(ip)) else None
    except ValueError:
        return None if _resolve_safe(host) is not None else f"DNS 解析失败或指向私网 (SSRF 防御): {host}"


def _extract_path(obj: Any, path: str) -> Any:
    """点路径从嵌套 dict 抽取; 路径不存在返回 None; 空路径返回整体。"""
    if not path:
        return obj
    cur = obj
    for key in path.split("."):
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def _default_fetch(method: str, url: str, headers: Dict[str, str],
                   body_bytes: Optional[bytes], timeout: int, max_bytes: int):
    """SSRF 安全的真实请求 (stdlib urllib, 禁跳转)。返回 (status:int, text:str)。"""
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None  # 不跟跳转 (避开间接跳内网)

    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(url, data=body_bytes, method=method, headers=headers)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read(max_bytes + 1)[:max_bytes]
        return getattr(resp, "status", 200), raw.decode("utf-8", errors="replace")


def make_http_tool(
    spec: HttpToolSpec, fetch: Optional[Callable] = None,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """spec → agent 工具 callable(payload)->envelope。

    流程: 占位填充 url → 拼 query → **SSRF 校验** → headers/auth → JSON body →
    fetch(默认 SSRF 安全 urllib, 可注入) → 解析 + `response_path` 抽取 → 标准 envelope。
    """
    do_fetch = fetch or _default_fetch

    def tool(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = payload or {}
        # 1. 占位填充 (payload 值 URL 转义)
        try:
            url = spec.url.format(
                **{k: urllib.parse.quote(str(v), safe="") for k, v in payload.items()})
        except (KeyError, IndexError) as e:
            return {"code": "PARAM_MISSING", "message": f"url 占位缺参数: {e}",
                    "data": None, "retryable": False}
        # 2. query string
        q = {k: payload[k] for k in spec.query if k in payload}
        if q:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(q)
        # 3. SSRF 校验
        err = _url_ssrf_error(url)
        if err:
            return {"code": "PARAM_INVALID", "message": err, "data": None, "retryable": False}
        # 4. headers + auth
        headers = {"User-Agent": "anything-agent/1.0", **dict(spec.headers)}
        if spec.auth_header and spec.auth_value:
            headers[spec.auth_header] = spec.auth_value
        # 5. body (仅写方法)
        method = (spec.method or "GET").upper()
        body_bytes = None
        if spec.body and method in ("POST", "PUT", "PATCH"):
            body_obj = {k: payload[k] for k in spec.body if k in payload}
            body_bytes = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        # 6. fetch
        try:
            status, text = do_fetch(method, url, headers, body_bytes, spec.timeout, spec.max_bytes)
        except Exception as e:
            return {"code": "TOOL_CALL_FAILED", "message": f"外部请求失败: {e}",
                    "data": {"url": url}, "retryable": True}
        # 7. 解析 + 抽取 (非 JSON → 原文)
        try:
            extracted = _extract_path(json.loads(text), spec.response_path)
        except (ValueError, TypeError):
            extracted = text
        return {"code": "SUCCESS", "message": "ok",
                "data": {"status": status, "result": extracted}, "retryable": False}

    return tool


class HttpToolProvider(ExternalToolProvider):
    """从 HttpToolSpec 列表暴露 HTTP 外部工具。"""

    def __init__(self, specs: List[HttpToolSpec]):
        self.specs = specs

    def discover(self) -> List[ExternalToolDef]:
        out: List[ExternalToolDef] = []
        for s in self.specs:
            try:
                out.append(ExternalToolDef(
                    name=s.name, func=make_http_tool(s),
                    description=s.description or f"外部 HTTP 工具: {s.method} {s.url}",
                    requires_approval=s.requires_approval, source="http",
                ))
            except Exception:
                continue  # fail-safe: 单个 spec 坏不影响其余
        return out
