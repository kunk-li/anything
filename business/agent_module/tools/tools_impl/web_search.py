# -*- coding: utf-8 -*-
"""
Tool: web_search (Task HHH #94)

通用 Web 搜索 — DuckDuckGo HTML 端点免 API key, 失败时 fallback Wikipedia.
跟 wikipedia_tool 互补: wikipedia 是百科 / 实体查询, web_search 是"查最新 X" /
跨多源信息汇总.

设计 — 类似 Claude 的 web_search / OpenAI 的 browse 工具:
    payload {"query": str, "top_k": int, "lang": "zh"|"en", "fetch_snippet": bool}
    成功 data {"query", "results": [{"title", "url", "snippet"}, ...], "source"}

DuckDuckGo HTML 端点 (html.duckduckgo.com/html/?q=...) 走 HTML scrape, 比官方
JSON instant answer 完整. 失败 (超时/被封 403) 时降级到 Wikipedia + 提示.

URL/IP 安全 — 继承 http_get 的 SSRF 防御 (走 duckduckgo.com 公共域名, 不让用户
注入私网地址).
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List


_DUCKDUCKGO_HTML = "https://html.duckduckgo.com/html/"
_UA = "Mozilla/5.0 (compatible; anything-agent/1.0)"


def web_search(payload: Dict[str, Any]) -> Dict[str, Any]:
    """通用 Web 搜索. payload:
        {"query": str, "top_k": int=5, "lang": "zh"|"en", "fetch_snippet": bool=True}

    成功返回 data: {"query", "results": [{"title", "url", "snippet"}, ...], "source": "duckduckgo"|"wikipedia_fallback"}
    无网时 fallback wikipedia_tool; 仍失败返回 TOOL_CALL_FAILED.
    """
    query = str(payload.get("query") or "").strip()
    if not query:
        return {"code": "PARAM_MISSING", "message": "query 不能为空",
                "data": None, "retryable": False}

    top_k = max(1, min(int(payload.get("top_k", 5) or 5), 20))
    lang = str(payload.get("lang") or "zh").lower()
    fetch_snippet = bool(payload.get("fetch_snippet", True))

    # 1. DuckDuckGo HTML 主路径
    try:
        results = _duckduckgo_search(query, top_k=top_k, lang=lang,
                                     want_snippet=fetch_snippet)
        if results:
            return {
                "code": "SUCCESS", "message": "ok",
                "data": {"query": query, "results": results,
                         "source": "duckduckgo", "count": len(results)},
                "retryable": False,
            }
    except Exception as e:
        # DuckDuckGo 失败 → fallback wikipedia
        _last_err = str(e)
    else:
        _last_err = "no results"

    # 2. fallback: wikipedia 一条
    try:
        from .wikipedia import wikipedia_tool
        wiki = wikipedia_tool({"query": query, "lang": lang, "max_chars": 600})
        if wiki.get("code") == "SUCCESS" and (wiki.get("data") or {}).get("title"):
            d = wiki["data"]
            return {
                "code": "SUCCESS", "message": "ok",
                "data": {
                    "query": query,
                    "results": [{
                        "title": d.get("title") or "",
                        "url": d.get("url") or "",
                        "snippet": d.get("summary") or "",
                    }],
                    "source": "wikipedia_fallback",
                    "count": 1,
                    "fallback_reason": f"duckduckgo: {_last_err}",
                },
                "retryable": False,
            }
    except Exception:
        pass

    return {
        "code": "TOOL_CALL_FAILED",
        "message": f"web_search 失败 (可能无网或被封): {_last_err}",
        "data": None,
        "retryable": True,
    }


def _duckduckgo_search(
    query: str, top_k: int = 5, lang: str = "zh", want_snippet: bool = True,
) -> List[Dict[str, str]]:
    """DuckDuckGo HTML scrape — 返回结果列表.

    走 POST 比 GET 更稳定 (GET 经常返回 redirect 页). HTML 结构相对稳定:
        <a class="result__a" href="<wrapped_url>">title</a>
        <a class="result__snippet">snippet</a>
    URL 是 DDG 转发的 /l/?uddg=<encoded>; 这里解出真实 URL.
    """
    # 区域: cn-zh (中文), us-en (英文), wt-wt 全球默认
    region = "cn-zh" if lang.startswith("zh") else "wt-wt"
    body = urllib.parse.urlencode({
        "q": query,
        "kl": region,
        "df": "",  # 时间过滤
    }).encode("utf-8")
    req = urllib.request.Request(
        _DUCKDUCKGO_HTML,
        data=body,
        headers={
            "User-Agent": _UA,
            "Accept": "text/html",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        text = resp.read().decode("utf-8", errors="replace")

    # 解析 — 不引依赖 (bs4 / lxml), 用纯正则.
    # 块标志: <div class="result__body"> ... </div>  内含 result__a + result__snippet
    results: List[Dict[str, str]] = []
    # 每个 result 的 anchor
    a_pattern = re.compile(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    a_matches = a_pattern.findall(text)
    snippet_matches = snippet_pattern.findall(text) if want_snippet else []

    for i, (href, title_html) in enumerate(a_matches):
        if len(results) >= top_k:
            break
        real_url = _unwrap_ddg_url(href)
        title = _strip_html(title_html).strip()
        snippet = ""
        if want_snippet and i < len(snippet_matches):
            snippet = _strip_html(snippet_matches[i]).strip()
        if not title and not real_url:
            continue
        results.append({
            "title": title,
            "url": real_url,
            "snippet": snippet[:300],
        })
    return results


def _unwrap_ddg_url(href: str) -> str:
    """DDG 把所有 result URL wrap 成 //duckduckgo.com/l/?uddg=<encoded>&... 形式.
    解出真实 URL."""
    if not href:
        return ""
    # 补全协议
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urllib.parse.urlparse(href)
        if "duckduckgo.com" in (parsed.netloc or "") and parsed.path.endswith("/l/"):
            qs = urllib.parse.parse_qs(parsed.query or "")
            uddg = qs.get("uddg") or qs.get("u")
            if uddg:
                return urllib.parse.unquote(uddg[0])
        return href
    except Exception:
        return href


def _strip_html(s: str) -> str:
    """简单 strip HTML tags + 解 entity. 不引 bs4."""
    s = re.sub(r"<[^>]+>", "", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
