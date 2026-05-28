# -*- coding: utf-8 -*-
"""
Tool: json_query (Task MM #73)
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
# 7. json_query — 简化 JSONPath 查询
# ============================================================

def _json_query_walk(data: Any, parts: List[str]) -> Any:
    """递归用 parts 逐级取值. parts 元素支持:
        - "key"      字典键
        - "[0]"      数组下标 (整数)
        - "*"        遍历当前层所有子项, 返回 list (扁平化)
    """
    if not parts:
        return data
    head, *rest = parts

    # 数组下标 [n]
    if re.fullmatch(r"\[-?\d+\]", head):
        idx = int(head[1:-1])
        if not isinstance(data, list):
            raise ValueError(f"路径段 {head!r} 期望 list, 实际 {type(data).__name__}")
        if not (-len(data) <= idx < len(data)):
            raise ValueError(f"数组下标越界: {idx}")
        return _json_query_walk(data[idx], rest)

    # 通配符 *
    if head == "*":
        if isinstance(data, list):
            return [_json_query_walk(item, rest) for item in data]
        if isinstance(data, dict):
            return [_json_query_walk(v, rest) for v in data.values()]
        raise ValueError(f"路径段 * 期望 list/dict, 实际 {type(data).__name__}")

    # 字典键
    if not isinstance(data, dict):
        raise ValueError(f"路径段 {head!r} 期望 dict, 实际 {type(data).__name__}")
    if head not in data:
        raise KeyError(f"找不到键 {head!r}")
    return _json_query_walk(data[head], rest)


def json_query(payload: Dict[str, Any]) -> Dict[str, Any]:
    """简化 JSONPath. payload:
        data: dict / list / 任意 JSON 值 (二选一)
        json_text: str — 当 data 没传时, 这里解析为 JSON 后用
        path: str, 点分键名 + [n] 数组下标 + * 通配符
              例 "user.name", "items.[0].title", "results.*.score"

    返回 data: {"path": str, "result": Any}
    """
    path = payload.get("path", "")
    if not isinstance(path, str):
        return {"code": "PARAM_INVALID", "message": "path 必须是字符串", "data": None, "retryable": False}

    if "data" in payload:
        data = payload["data"]
    elif "json_text" in payload:
        try:
            data = json.loads(str(payload["json_text"]))
        except Exception as e:
            return {"code": "PARAM_INVALID", "message": f"json_text 解析失败: {e}",
                    "data": None, "retryable": False}
    else:
        return {"code": "PARAM_MISSING", "message": "需要 data 或 json_text", "data": None, "retryable": False}

    # 切分路径, 例 "items.[0].title" -> ["items", "[0]", "title"]
    parts: List[str] = []
    for token in path.split("."):
        token = token.strip()
        if not token:
            continue
        # 处理 "key[0]" 这种, 拆出来
        m = re.match(r"^([^\[\]]*?)(\[-?\d+\])+$", token)
        if m:
            base = m.group(1)
            if base:
                parts.append(base)
            # 把所有 [n] 段都抽出来
            for ix in re.findall(r"\[-?\d+\]", token):
                parts.append(ix)
        else:
            parts.append(token)

    try:
        result = _json_query_walk(data, parts) if parts else data
    except (KeyError, ValueError) as e:
        return {"code": "TOOL_CALL_FAILED", "message": str(e), "data": None, "retryable": False}

    return {
        "code": "SUCCESS", "message": "ok",
        "data": {"path": path, "result": result},
        "retryable": False,
    }


