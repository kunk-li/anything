# -*- coding: utf-8 -*-
"""
Tool: regex_extract (Task MM #73)
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
# 5. regex_extract — 正则抽取
# ============================================================

_REGEX_FLAGS = {
    "i": re.IGNORECASE, "ignorecase": re.IGNORECASE,
    "m": re.MULTILINE,  "multiline": re.MULTILINE,
    "s": re.DOTALL,     "dotall": re.DOTALL,
    "u": re.UNICODE,    "unicode": re.UNICODE,
    "x": re.VERBOSE,    "verbose": re.VERBOSE,
}


def regex_extract(payload: Dict[str, Any]) -> Dict[str, Any]:
    """正则抽取工具.

    payload:
        text: 待搜索文本 (必填)
        pattern: 正则表达式 (必填)
        flags: list[str] 或 str, 可选 ["i","m","s",...] 或 "ims"
        max_matches: int 默认 50, 上限 500
        return_groups: bool 默认 True, True 返回 group dict, False 返回完整 match

    返回 data: {"matches": [...], "match_count": N, "pattern": str}
    """
    text = payload.get("text")
    pattern = payload.get("pattern")
    if not isinstance(text, str) or not text:
        return {"code": "PARAM_MISSING", "message": "text 不能为空", "data": None, "retryable": False}
    if not isinstance(pattern, str) or not pattern:
        return {"code": "PARAM_MISSING", "message": "pattern 不能为空", "data": None, "retryable": False}
    if len(text) > 100_000:
        return {"code": "PARAM_INVALID", "message": "text 过长 (>100K)", "data": None, "retryable": False}
    if len(pattern) > 500:
        return {"code": "PARAM_INVALID", "message": "pattern 过长", "data": None, "retryable": False}

    raw_flags = payload.get("flags") or []
    flag_value = 0
    flag_items: List[str] = []
    if isinstance(raw_flags, str):
        flag_items = list(raw_flags)
    elif isinstance(raw_flags, list):
        flag_items = [str(x) for x in raw_flags]
    for f in flag_items:
        v = _REGEX_FLAGS.get(f.lower())
        if v:
            flag_value |= v

    max_matches = max(1, min(int(payload.get("max_matches", 50) or 50), 500))
    return_groups = bool(payload.get("return_groups", True))

    try:
        compiled = re.compile(pattern, flag_value)
    except re.error as e:
        return {"code": "PARAM_INVALID", "message": f"正则编译失败: {e}", "data": None, "retryable": False}

    matches: List[Any] = []
    for i, m in enumerate(compiled.finditer(text)):
        if i >= max_matches:
            break
        if return_groups and m.groups():
            try:
                matches.append({
                    "match": m.group(0),
                    "groups": list(m.groups()),
                    "named": m.groupdict(),
                    "span": [m.start(), m.end()],
                })
            except IndexError:
                matches.append({"match": m.group(0), "span": [m.start(), m.end()]})
        else:
            matches.append({"match": m.group(0), "span": [m.start(), m.end()]})

    return {
        "code": "SUCCESS", "message": "ok",
        "data": {
            "pattern": pattern,
            "match_count": len(matches),
            "matches": matches,
            "truncated": len(matches) >= max_matches,
        },
        "retryable": False,
    }


