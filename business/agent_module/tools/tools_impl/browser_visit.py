# -*- coding: utf-8 -*-
"""
Tool: browser_visit (Task TTTT-5 #142)

用 Playwright headless 浏览器访问 URL, 抽 text / 截图. 能跑动态 JS, 比
http_get 强很多 (后者只 GET 静态 HTML).

依赖:
    pip install playwright
    playwright install chromium    # 一次性装浏览器 (~150MB)

安全:
    - URL 必须 http/https
    - 禁内网 IP (SSRF 防御复用 http_get 的逻辑)
    - 超时上限 60s
    - 文本截断
"""
from __future__ import annotations

import base64
import ipaddress
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

# 复用 http_get 已加固的 SSRF 守卫: IP 字面量走 _is_private_ip, 域名走
# _resolve_safe (DNS 解析全部 A/AAAA 记录, 任一私网/环回/链路本地/保留/IPv6 环回即拒).
# 取代原先只认几个 IPv4 前缀 + "localhost" 的正则 (IPv6 / 数字 IP / 任意指向内网的
# 域名都能绕过).
from .http_get import _is_private_ip, _resolve_safe

_MAX_TEXT = 8000


def browser_visit(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Input:
      url: str                必填
      wait_selector: str?     额外等某个 CSS selector 出现 (默 'body')
      wait_ms: int?           额外等加载毫秒, 默 1500
      action: "text"|"html"|"screenshot"|"all"  默 "text"
      screenshot_full_page: bool?    截整页 (默 False)
      timeout_seconds: int?   默 30, 上限 60
    Output:
      data: { url, title, text?, html?, screenshot_b64?, screenshot_path?, description }
    """
    trace_id = payload.get("trace_id")
    url = (payload.get("url") or "").strip()
    if not url:
        return _err("PARAM_MISSING", "url 必填", trace_id)

    pu = urlparse(url)
    if pu.scheme not in ("http", "https"):
        return _err("INVALID_URL", f"只支持 http/https. 收到: {pu.scheme}://", trace_id)
    host = pu.hostname or ""
    if not host:
        return _err("INVALID_URL", "url 缺 host", trace_id)
    # SSRF 守卫: IP 字面量直接判私网; 否则 DNS 解析全部记录, 任一私网即拒.
    try:
        ip = ipaddress.ip_address(host)
        forbidden = _is_private_ip(str(ip))
    except ValueError:
        # 是域名, 走 DNS (覆盖 IPv6 / 数字 IP / 指向内网的任意域名)
        forbidden = _resolve_safe(host) is None
    if forbidden:
        return _err(
            "FORBIDDEN_HOST",
            f"内网/环回地址或 DNS 解析失败 禁访 (SSRF 防御). host={host}",
            trace_id,
        )

    action = (payload.get("action") or "text").lower()
    if action not in ("text", "html", "screenshot", "all"):
        return _err("PARAM_INVALID", f"action 必须是 text|html|screenshot|all. 收到: {action}", trace_id)

    wait_selector = payload.get("wait_selector") or "body"
    wait_ms = max(0, min(10000, int(payload.get("wait_ms") or 1500)))
    timeout_s = max(5, min(60, int(payload.get("timeout_seconds") or 30)))
    full_page = bool(payload.get("screenshot_full_page", False))

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
    except ImportError:
        return _err(
            "MISSING_DEPS",
            "playwright 未装. 跑: pip install playwright && playwright install chromium",
            trace_id,
        )

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
                    )
                )
                page = ctx.new_page()
                try:
                    page.goto(url, timeout=timeout_s * 1000, wait_until="domcontentloaded")
                except PWTimeoutError:
                    return _err("TIMEOUT", f"页面 {timeout_s}s 内未加载完", trace_id)
                # 等动态 selector / 额外 wait
                try:
                    page.wait_for_selector(wait_selector, timeout=max(2000, wait_ms))
                except PWTimeoutError:
                    pass  # selector 没等到不致命, 继续抽
                if wait_ms:
                    page.wait_for_timeout(wait_ms)

                title = page.title() or ""
                data: Dict[str, Any] = {
                    "url": url,
                    "title": title,
                }

                if action in ("text", "all"):
                    text = page.evaluate(
                        "() => document.body ? document.body.innerText : ''"
                    )
                    truncated = False
                    if text and len(text) > _MAX_TEXT:
                        text = text[:_MAX_TEXT]
                        truncated = True
                    data["text"] = text
                    data["text_truncated"] = truncated

                if action in ("html", "all"):
                    html = page.content() or ""
                    if len(html) > 200_000:
                        html = html[:200_000]
                        data["html_truncated"] = True
                    data["html"] = html

                if action in ("screenshot", "all"):
                    # 截图存到 uploads/screenshots/
                    out_dir = Path.cwd() / "uploads" / "screenshots"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    fname = f"screenshot_{int(time.time())}_{os.urandom(3).hex()}.png"
                    fpath = out_dir / fname
                    page.screenshot(path=str(fpath), full_page=full_page)
                    data["screenshot_path"] = str(fpath.relative_to(Path.cwd()))
                    # 同时给 base64 让 LLM 看 (但很大, 只在 action=screenshot 时返)
                    if action == "screenshot":
                        try:
                            data["screenshot_b64"] = base64.b64encode(fpath.read_bytes()).decode("ascii")
                        except Exception:
                            pass

                data["description"] = f"抓取 {url} (title={title[:60]!r})"
                if "text" in data:
                    data["description"] += f", text 长度 {len(data['text'])}"
                if "screenshot_path" in data:
                    data["description"] += f", 截图保存 {data['screenshot_path']}"

                return {
                    "code": "SUCCESS", "message": "ok",
                    "data": data,
                    "trace_id": trace_id, "retryable": False, "details": None,
                }
            finally:
                browser.close()
    except Exception as e:
        msg = str(e)
        # 检测浏览器没装的常见错误
        if "Executable doesn't exist" in msg or "BrowserType.launch" in msg:
            return _err(
                "BROWSER_NOT_INSTALLED",
                "Chromium 未装. 跑: playwright install chromium",
                trace_id,
            )
        return _err("BROWSER_FAILED", f"浏览器执行失败: {msg[:200]}", trace_id)


def _err(code: str, message: str, trace_id: Optional[str]) -> Dict[str, Any]:
    return {
        "code": code, "message": message, "data": None,
        "trace_id": trace_id, "retryable": False, "details": None,
    }


BROWSER_VISIT_DESCRIPTION = (
    '用 Playwright headless Chromium 抓动态网页. input: {"url": str, '
    '"action": "text"|"html"|"screenshot"|"all"? (默 "text"), '
    '"wait_selector": str? (CSS), "wait_ms": int? (默 1500), '
    '"timeout_seconds": int? (默 30, 上限 60), '
    '"screenshot_full_page": bool?}. '
    "比 http_get 强: 能跑 JS, 抓动态内容, 截图. SSRF 防御 (禁内网). "
    "需要 playwright install chromium. 适合: 抓单页应用 / 截图存档 / 看真实渲染."
)
