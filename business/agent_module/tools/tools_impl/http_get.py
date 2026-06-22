# -*- coding: utf-8 -*-
"""
Tool: http_get (Task MM #73)
"""

from __future__ import annotations

import ast
import http.client
import ipaddress
import json
import math
import operator
import re
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

# ============================================================
# 8. http_get — 受控 GET (SSRF 防御)
# ============================================================

def _is_private_ip(ip_str: str) -> bool:
    """判断 IP 是否私网 / 环回 / 链路本地 / 多播等不该被 SSRF 出去的地址."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def _resolve_safe(host: str) -> Optional[str]:
    """DNS 解析 host, 检查所有 A/AAAA 记录是否私网. 任一私网 -> 拒绝 (返回 None)."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None
    ips = {ai[4][0] for ai in infos}
    for ip in ips:
        if _is_private_ip(ip):
            return None
    return ", ".join(sorted(ips))


def _resolve_pinned(host: str) -> Optional[Tuple[int, str, str]]:
    """DNS 解析 host, 校验全部 A/AAAA 记录. 全部安全 -> 返回 (family, pin_ip, 展示串);
    任一私网 -> 拒绝 (None).

    与 _resolve_safe 不同: 这里额外挑出一个已校验的具体 IP 作为"连接锁定地址",
    交给 _PinnedHTTPConnection / _PinnedHTTPSConnection 实际拨号, 杜绝
    "校验时解析到公网 IP、连接时 DNS 重绑定到内网 IP" 的 TOCTOU/DNS-rebinding 旁路.
    """
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None
    if not infos:
        return None
    ips = {ai[4][0] for ai in infos}
    for ip in ips:
        if _is_private_ip(ip):
            return None
    # 选第一条记录的 family + IP 作为锁定地址 (已确认其与所有同名记录都非私网)
    pin_family = infos[0][0]
    pin_ip = infos[0][4][0]
    return pin_family, pin_ip, ", ".join(sorted(ips))


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """把 TCP 连接锁定到调用方校验过的 IP, 但 Host 头仍用原 hostname.

    standard HTTPConnection.connect() 会自己再做一次 DNS 解析, 那正是
    DNS-rebinding 的攻击窗口. 这里直接对锁定 IP 拨号, 保证"连的就是审过的 IP".
    """

    def __init__(self, host, *args, pin_address=None, **kwargs):
        super().__init__(host, *args, **kwargs)
        self._pin_address = pin_address  # (family, ip)

    def connect(self):
        family, ip = self._pin_address
        self.sock = socket.create_connection(
            (ip, self.port), self.timeout, self.source_address,
        )
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS 版: 锁定 IP 拨号, TLS SNI / 证书校验仍按原 hostname (server_hostname)."""

    def __init__(self, host, *args, pin_address=None, **kwargs):
        super().__init__(host, *args, **kwargs)
        self._pin_address = pin_address  # (family, ip)

    def connect(self):
        family, ip = self._pin_address
        sock = socket.create_connection(
            (ip, self.port), self.timeout, self.source_address,
        )
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
        server_hostname = self.host
        # 与 hostname 同名的 IP 连接证书也按 hostname 校验
        self.sock = self._context.wrap_socket(sock, server_hostname=server_hostname)


def _make_pinned_handlers(pin_family: int, pin_ip: str):
    """生成把连接锁定到 pin_ip 的 http/https handler, 喂给 build_opener."""
    pin_address = (pin_family, pin_ip)

    class _PinnedHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(
                lambda host, **kw: _PinnedHTTPConnection(host, pin_address=pin_address, **kw),
                req,
            )

    class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(
                lambda host, **kw: _PinnedHTTPSConnection(host, pin_address=pin_address, **kw),
                req,
            )

    return _PinnedHTTPHandler(), _PinnedHTTPSHandler()


def http_get(payload: Dict[str, Any]) -> Dict[str, Any]:
    """受控 HTTP GET. SSRF 防御:
        - 只允许 http/https
        - DNS 解析后任一 IP 落入私网/环回/链路本地 -> 拒
        - max_bytes 默认 1MB, 上限 10MB
        - max_redirects = 0 (不跟跳转, 避开间接跳到内网的攻击)
        - timeout 默认 8s, 上限 30s

    payload: {"url": str, "max_bytes": int = 1048576, "timeout": int = 8}
    返回 data: {"url", "status", "content_type", "text", "truncated"}
    """
    url = str(payload.get("url") or "").strip()
    if not url:
        return {"code": "PARAM_MISSING", "message": "url 不能为空", "data": None, "retryable": False}

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as e:
        return {"code": "PARAM_INVALID", "message": f"url 解析失败: {e}", "data": None, "retryable": False}

    if parsed.scheme not in ("http", "https"):
        return {"code": "PARAM_INVALID", "message": "仅允许 http / https 协议",
                "data": None, "retryable": False}
    host = parsed.hostname or ""
    if not host:
        return {"code": "PARAM_INVALID", "message": "url 缺 host", "data": None, "retryable": False}

    # 拒绝直接写 IP 私网; 同时解析出"连接锁定地址" pin_family/pin_ip,
    # 后面真正拨号就用它, 杜绝 DNS-rebinding / TOCTOU 旁路.
    try:
        ip = ipaddress.ip_address(host)
        if _is_private_ip(str(ip)):
            return {"code": "PARAM_INVALID",
                    "message": f"拒绝私网 IP (SSRF 防御): {host}",
                    "data": None, "retryable": False}
        resolved = str(ip)
        pin_family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        pin_ip = str(ip)
    except ValueError:
        # 是 hostname, 走 DNS 并锁定一个已校验的 IP
        pinned = _resolve_pinned(host)
        if pinned is None:
            return {"code": "PARAM_INVALID",
                    "message": f"DNS 解析失败或指向私网 (SSRF 防御): {host}",
                    "data": None, "retryable": False}
        pin_family, pin_ip, resolved = pinned

    max_bytes = max(1024, min(int(payload.get("max_bytes", 1048576) or 1048576), 10 * 1024 * 1024))
    timeout = max(1, min(int(payload.get("timeout", 8) or 8), 30))

    # 禁用 redirect: 用 build_opener + 自定义 handler
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None  # 不跟跳转

    pinned_http, pinned_https = _make_pinned_handlers(pin_family, pin_ip)
    opener = urllib.request.build_opener(_NoRedirect(), pinned_http, pinned_https)
    req = urllib.request.Request(url, headers={"User-Agent": "anything-agent/1.0"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read(max_bytes + 1)
            truncated = len(raw) > max_bytes
            raw = raw[:max_bytes]
            # 尝试用 content-type 里的 charset 解码, 失败回退 utf-8 (errors=replace)
            charset = "utf-8"
            for piece in content_type.split(";"):
                piece = piece.strip()
                if piece.lower().startswith("charset="):
                    charset = piece.split("=", 1)[1].strip().lower()
                    break
            try:
                text = raw.decode(charset, errors="replace")
            except Exception:
                text = raw.decode("utf-8", errors="replace")
            return {
                "code": "SUCCESS", "message": "ok",
                "data": {
                    "url": url,
                    "resolved_ip": resolved,
                    "status": resp.status,
                    "content_type": content_type,
                    "text": text,
                    "byte_count": len(raw),
                    "truncated": truncated,
                },
                "retryable": False,
            }
    except urllib.request.HTTPError as e:
        return {"code": "TOOL_CALL_FAILED",
                "message": f"HTTP {e.code} {e.reason}",
                "data": {"url": url, "status": e.code}, "retryable": True}
    except Exception as e:
        return {"code": "TOOL_CALL_FAILED", "message": f"请求异常: {e}",
                "data": None, "retryable": True}


