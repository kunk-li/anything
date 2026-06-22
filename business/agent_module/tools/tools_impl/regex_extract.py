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
# ⚠️ ReDoS 约束: 用户可控 pattern + 用户可控 text 是经典 ReDoS 面 (catastrophic
#    backtracking 可让单次匹配 hang 秒级以上)。stdlib `re` 的匹配跑在 C 层、无法
#    被 Python 线程强杀 (与 python_sandbox 同样的"线程杀不掉"困境)。
#    根治: 优先用第三方 `regex` 模块的 `timeout=` 参数 —— 它在 C 层引擎内部检查
#    deadline 并抛 TimeoutError, 是真正能打断 runaway 匹配的硬阀。
#    `regex` 不可用时降级到 `re`, 同时把 text 上限压到更低并沿用与 python_sandbox
#    一致的"线程杀不掉"告警。
try:
    import regex as _regex  # type: ignore
    _HAS_REGEX = True
except Exception:  # pragma: no cover - 环境无 regex 时降级
    _regex = None  # type: ignore
    _HAS_REGEX = False

# 单次匹配硬超时 (秒)。仅在 `regex` 可用时真正生效 (C 层 deadline 检查)。
_REGEX_MATCH_TIMEOUT_S = 2.0
# text 上限: 有 regex 硬超时时放宽, 无硬超时 (裸 re) 时压低以缩小 ReDoS 窗口。
_TEXT_LIMIT_WITH_TIMEOUT = 100_000
_TEXT_LIMIT_NO_TIMEOUT = 20_000

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
    text_limit = _TEXT_LIMIT_WITH_TIMEOUT if _HAS_REGEX else _TEXT_LIMIT_NO_TIMEOUT
    if len(text) > text_limit:
        return {
            "code": "PARAM_INVALID",
            "message": f"text 过长 (>{text_limit})",
            "data": None,
            "retryable": False,
        }
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

    # 用 regex (有硬超时) 否则降级 re; 二者 flag int 与 match 对象 API 兼容。
    engine = _regex if _HAS_REGEX else re
    try:
        compiled = engine.compile(pattern, flag_value)
    except engine.error as e:
        return {"code": "PARAM_INVALID", "message": f"正则编译失败: {e}", "data": None, "retryable": False}

    # 多取 1 条用于判定"真溢出": 命中数恰好等于 max_matches 但后面没有第 (max+1)
    # 条时不算截断, 避免 false truncated=True。
    try:
        if _HAS_REGEX:
            match_iter = compiled.finditer(text, timeout=_REGEX_MATCH_TIMEOUT_S)
        else:
            match_iter = compiled.finditer(text)
        raw_matches: List[Any] = []
        for m in match_iter:
            raw_matches.append(m)
            if len(raw_matches) > max_matches:
                break
    except TimeoutError:
        # 仅 regex 引擎会到这里: C 层 deadline 命中, runaway 匹配被真正打断。
        return {
            "code": "TOOL_CALL_FAILED",
            "message": f"正则匹配超时 (>{_REGEX_MATCH_TIMEOUT_S}s), 疑似 catastrophic backtracking",
            "data": None,
            "retryable": False,
        }

    truncated = len(raw_matches) > max_matches
    if truncated:
        raw_matches = raw_matches[:max_matches]

    matches: List[Any] = []
    for m in raw_matches:
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
            "truncated": truncated,
        },
        "retryable": False,
    }


