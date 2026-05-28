# -*- coding: utf-8 -*-
"""
Tool: wikipedia (Task MM #73)
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
# 3. wikipedia_tool — REST API, 无外部依赖 (urllib only)
# ============================================================

def wikipedia_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Wikipedia 摘要查询. payload:
        {"query": str, "lang": "zh"|"en", "max_chars": 800}

    成功返回 data: {"query", "title", "summary", "url", "lang"}
    无网时返回 TOOL_CALL_FAILED + message 告知 (Agent 应降级到 llm_generate)。
    """
    query = str(payload.get("query") or "").strip()
    if not query:
        return {"code": "PARAM_MISSING", "message": "query 不能为空", "data": None, "retryable": False}
    lang = str(payload.get("lang") or "zh").lower()
    if lang not in ("zh", "en", "ja", "ko", "fr", "de", "es"):
        lang = "zh"
    max_chars = max(50, min(int(payload.get("max_chars", 800) or 800), 4000))

    # Wikipedia REST: /api/rest_v1/page/summary/<title>
    # 但 user 查询是关键词不一定是 title, 用 search 接口先 resolve
    try:
        # 1. 搜索 -> 拿到第一条 title
        search_url = (
            f"https://{lang}.wikipedia.org/w/api.php"
            f"?action=opensearch&search={urllib.parse.quote(query)}"
            f"&limit=1&namespace=0&format=json"
        )
        req = urllib.request.Request(search_url, headers={"User-Agent": "anything-agent/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            search_data = json.loads(resp.read().decode("utf-8"))
        titles = search_data[1] if isinstance(search_data, list) and len(search_data) > 1 else []
        if not titles:
            return {
                "code": "SUCCESS", "message": "no results",
                "data": {"query": query, "title": None, "summary": "", "url": None, "lang": lang},
                "retryable": False,
            }
        title = titles[0]

        # 2. 拉摘要
        sum_url = (
            f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
            f"{urllib.parse.quote(title.replace(' ', '_'))}"
        )
        req2 = urllib.request.Request(sum_url, headers={"User-Agent": "anything-agent/1.0"})
        with urllib.request.urlopen(req2, timeout=8) as resp2:
            sum_data = json.loads(resp2.read().decode("utf-8"))

        extract = str(sum_data.get("extract", "") or "")[:max_chars]
        url = (
            sum_data.get("content_urls", {}).get("desktop", {}).get("page")
            or f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title)}"
        )
        return {
            "code": "SUCCESS", "message": "ok",
            "data": {
                "query": query,
                "title": title,
                "summary": extract,
                "url": url,
                "lang": lang,
            },
            "retryable": False,
        }
    except Exception as e:
        # 离线 / 超时 / DNS 失败 — 返回 TOOL_CALL_FAILED, agent 应该走 llm_generate 兜底
        return {
            "code": "TOOL_CALL_FAILED",
            "message": f"wikipedia 查询失败 (可能无网): {e}",
            "data": None,
            "retryable": True,
        }


