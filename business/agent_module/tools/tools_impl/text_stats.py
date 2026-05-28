# -*- coding: utf-8 -*-
"""
Tool: text_stats (Task MM #73)
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
# 6. text_stats — 文本统计
# ============================================================

def text_stats(payload: Dict[str, Any]) -> Dict[str, Any]:
    """文本统计 — 字数 / 词数 / 行数 / Unicode 块占比.

    payload: {"text": str}
    返回:
        char_count            总字符数 (含空格)
        char_count_no_space   不含空白
        word_count            按空白分割的"词"数 (中文 / 日韩按字算)
        line_count            行数
        cjk_chars             中日韩字符数
        ascii_chars           ASCII 字符数 (含字母数字标点)
        digit_chars           数字字符数
    """
    text = payload.get("text")
    if not isinstance(text, str):
        return {"code": "PARAM_MISSING", "message": "text 不是字符串", "data": None, "retryable": False}
    if len(text) > 1_000_000:
        return {"code": "PARAM_INVALID", "message": "text 过长 (>1M)", "data": None, "retryable": False}

    cjk_chars = 0
    ascii_chars = 0
    digit_chars = 0
    no_space_chars = 0
    for ch in text:
        cp = ord(ch)
        if not ch.isspace():
            no_space_chars += 1
        if ch.isdigit():
            digit_chars += 1
        if cp < 128:
            ascii_chars += 1
        # CJK 主块: U+4E00-9FFF 中日韩统一表意, +3000-303F 中日韩符号
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0x3000 <= cp <= 0x303F:
            cjk_chars += 1

    return {
        "code": "SUCCESS", "message": "ok",
        "data": {
            "char_count": len(text),
            "char_count_no_space": no_space_chars,
            "word_count": len(text.split()),
            "line_count": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
            "cjk_chars": cjk_chars,
            "ascii_chars": ascii_chars,
            "digit_chars": digit_chars,
        },
        "retryable": False,
    }


