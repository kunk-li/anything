# -*- coding: utf-8 -*-
"""
Tool: datetime_tool (Task MM #73)
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
# 2. datetime_tool — 当前时间 / 时区算术
# ============================================================

def datetime_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    """时间工具.

    payload:
        {"op": "now"}                                  当前 UTC + 本地
        {"op": "now", "tz_offset_hours": 8}            指定时区
        {"op": "add", "iso": "2026-05-27T10:00", "days": 7, "hours": -3}
                                                       日期算术
        {"op": "diff", "iso_start": "...", "iso_end": "..."}
                                                       天/秒差

    返回 data: {iso, timestamp, weekday, op_specific...}
    """
    op = str(payload.get("op") or "now").lower()

    try:
        if op == "now":
            tz_offset = payload.get("tz_offset_hours")
            if tz_offset is not None:
                tz = timezone(timedelta(hours=float(tz_offset)))
            else:
                tz = timezone.utc
            dt = datetime.now(tz)
            return {
                "code": "SUCCESS", "message": "ok",
                "data": {
                    "iso": dt.isoformat(),
                    "timestamp": dt.timestamp(),
                    "weekday": dt.strftime("%A"),
                    "tz_offset_hours": (tz.utcoffset(dt) or timedelta()).total_seconds() / 3600,
                },
                "retryable": False,
            }

        if op == "add":
            iso = str(payload.get("iso") or "").strip()
            if not iso:
                return {"code": "PARAM_MISSING", "message": "add 需要 iso", "data": None, "retryable": False}
            base = datetime.fromisoformat(iso)
            delta = timedelta(
                days=float(payload.get("days", 0)),
                hours=float(payload.get("hours", 0)),
                minutes=float(payload.get("minutes", 0)),
                seconds=float(payload.get("seconds", 0)),
            )
            new_dt = base + delta
            return {
                "code": "SUCCESS", "message": "ok",
                "data": {
                    "iso": new_dt.isoformat(),
                    "weekday": new_dt.strftime("%A"),
                    "delta_seconds": delta.total_seconds(),
                },
                "retryable": False,
            }

        if op == "diff":
            s = str(payload.get("iso_start") or "").strip()
            e = str(payload.get("iso_end") or "").strip()
            if not s or not e:
                return {"code": "PARAM_MISSING", "message": "diff 需要 iso_start + iso_end", "data": None, "retryable": False}
            d1 = datetime.fromisoformat(s)
            d2 = datetime.fromisoformat(e)
            diff = d2 - d1
            return {
                "code": "SUCCESS", "message": "ok",
                "data": {
                    "seconds": diff.total_seconds(),
                    "days": diff.days,
                    "hours": diff.total_seconds() / 3600,
                },
                "retryable": False,
            }

        return {"code": "PARAM_INVALID", "message": f"未知 op: {op}", "data": None, "retryable": False}

    except ValueError as e:
        return {"code": "PARAM_INVALID", "message": str(e), "data": None, "retryable": False}
    except Exception as e:
        return {"code": "TOOL_CALL_FAILED", "message": str(e), "data": None, "retryable": False}


