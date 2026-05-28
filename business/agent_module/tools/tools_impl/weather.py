# -*- coding: utf-8 -*-
"""
Tool: weather (Task MM #73)
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
# 13. weather — Open-Meteo 免 key 天气查询
# ============================================================

def weather(payload: Dict[str, Any]) -> Dict[str, Any]:
    """天气查询. 用 Open-Meteo (https://open-meteo.com), 无需 API key, 公网开放。

    payload:
        location: 城市名 (中英都行, 通过 geocoding API resolve)
        OR latitude + longitude: 经纬度 (跳过 geocoding)

    返回 data: {
        "location": {"name", "country", "latitude", "longitude"},
        "current": {"temperature", "weathercode", "windspeed", "winddirection", "is_day", "time"},
    }
    """
    location = payload.get("location")
    lat = payload.get("latitude")
    lon = payload.get("longitude")

    if not location and (lat is None or lon is None):
        return {
            "code": "PARAM_MISSING",
            "message": "需要 location 字符串 或 latitude+longitude",
            "data": None, "retryable": False,
        }

    location_info = {"name": None, "country": None, "latitude": lat, "longitude": lon}

    try:
        # 1. 没经纬度时, 用 geocoding 解析
        if lat is None or lon is None:
            geo_url = (
                "https://geocoding-api.open-meteo.com/v1/search"
                f"?name={urllib.parse.quote(str(location))}&count=1&language=zh"
            )
            req = urllib.request.Request(geo_url, headers={"User-Agent": "anything-agent/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                geo_data = json.loads(resp.read().decode("utf-8"))
            results = geo_data.get("results") or []
            if not results:
                return {
                    "code": "SUCCESS", "message": "location not found",
                    "data": {"location": {"name": location}, "current": None},
                    "retryable": False,
                }
            top = results[0]
            location_info = {
                "name": top.get("name"),
                "country": top.get("country"),
                "latitude": top.get("latitude"),
                "longitude": top.get("longitude"),
            }
            lat = top.get("latitude")
            lon = top.get("longitude")

        # 2. 拉当前天气
        weather_url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}&current_weather=true"
        )
        req2 = urllib.request.Request(weather_url, headers={"User-Agent": "anything-agent/1.0"})
        with urllib.request.urlopen(req2, timeout=8) as resp2:
            wdata = json.loads(resp2.read().decode("utf-8"))

        current = wdata.get("current_weather") or {}
        return {
            "code": "SUCCESS", "message": "ok",
            "data": {
                "location": location_info,
                "current": {
                    "temperature_celsius": current.get("temperature"),
                    "weathercode": current.get("weathercode"),
                    "windspeed_kmh": current.get("windspeed"),
                    "winddirection_deg": current.get("winddirection"),
                    "is_day": bool(current.get("is_day", 1)),
                    "time": current.get("time"),
                },
            },
            "retryable": False,
        }
    except Exception as e:
        return {
            "code": "TOOL_CALL_FAILED",
            "message": f"天气查询失败 (可能无网): {e}",
            "data": None, "retryable": True,
        }


