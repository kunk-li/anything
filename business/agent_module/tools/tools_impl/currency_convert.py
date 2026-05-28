# -*- coding: utf-8 -*-
"""
Tool: currency_convert (Task MM #73)
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
# 14. currency_convert — ECB Frankfurter 免 key 汇率换算
# ============================================================

def currency_convert(payload: Dict[str, Any]) -> Dict[str, Any]:
    """汇率换算. 用 https://api.frankfurter.app (ECB 数据, 免 key, 公网开放)。

    payload:
        from_currency: 3 字母代码 (USD/EUR/CNY/JPY/GBP/...)
        to_currency: 同上
        amount: 数字 (默认 1.0)
        date: "YYYY-MM-DD" 或 "latest" (默认 latest)

    返回 data: {"from", "to", "amount", "converted", "rate", "date"}
    """
    from_cur = str(payload.get("from_currency") or "").strip().upper()
    to_cur = str(payload.get("to_currency") or "").strip().upper()
    if not from_cur or not to_cur:
        return {
            "code": "PARAM_MISSING",
            "message": "from_currency / to_currency 必填",
            "data": None, "retryable": False,
        }
    if len(from_cur) != 3 or len(to_cur) != 3:
        return {
            "code": "PARAM_INVALID",
            "message": "货币代码必须是 3 字母 ISO 4217",
            "data": None, "retryable": False,
        }
    amount = float(payload.get("amount", 1.0) or 1.0)
    date = str(payload.get("date") or "latest")

    if from_cur == to_cur:
        return {
            "code": "SUCCESS", "message": "ok (same currency)",
            "data": {
                "from": from_cur, "to": to_cur, "amount": amount,
                "converted": amount, "rate": 1.0, "date": date,
            },
            "retryable": False,
        }

    try:
        url = (
            f"https://api.frankfurter.app/{urllib.parse.quote(date)}"
            f"?from={from_cur}&to={to_cur}&amount={amount}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "anything-agent/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {
            "code": "TOOL_CALL_FAILED",
            "message": f"汇率查询失败: {e}",
            "data": None, "retryable": True,
        }

    rates = data.get("rates") or {}
    converted = rates.get(to_cur)
    if converted is None:
        return {
            "code": "PARAM_INVALID",
            "message": f"不支持的货币对: {from_cur} -> {to_cur}",
            "data": None, "retryable": False,
        }
    rate = converted / amount if amount else None
    return {
        "code": "SUCCESS", "message": "ok",
        "data": {
            "from": from_cur,
            "to": to_cur,
            "amount": amount,
            "converted": converted,
            "rate": rate,
            "date": data.get("date") or date,
        },
        "retryable": False,
    }


