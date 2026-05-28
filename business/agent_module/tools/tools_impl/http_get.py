# -*- coding: utf-8 -*-
"""
Tool: http_get (Task MM #73)
"""

from __future__ import annotations

import ast
import ipaddress
import json
import math
import operator
import re
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

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

    # 拒绝直接写 IP 私网
    try:
        ip = ipaddress.ip_address(host)
        if _is_private_ip(str(ip)):
            return {"code": "PARAM_INVALID",
                    "message": f"拒绝私网 IP (SSRF 防御): {host}",
                    "data": None, "retryable": False}
        resolved = str(ip)
    except ValueError:
        # 是 hostname, 走 DNS
        resolved = _resolve_safe(host)
        if resolved is None:
            return {"code": "PARAM_INVALID",
                    "message": f"DNS 解析失败或指向私网 (SSRF 防御): {host}",
                    "data": None, "retryable": False}

    max_bytes = max(1024, min(int(payload.get("max_bytes", 1048576) or 1048576), 10 * 1024 * 1024))
    timeout = max(1, min(int(payload.get("timeout", 8) or 8), 30))

    # 禁用 redirect: 用 build_opener + 自定义 handler
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None  # 不跟跳转

    opener = urllib.request.build_opener(_NoRedirect())
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


