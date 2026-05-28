# -*- coding: utf-8 -*-
"""
Tool: code_lint (Task MM #73)
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
# 10. code_lint — 多语言语法检查
# ============================================================

def code_lint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """代码语法检查 (静态, 不执行).

    payload:
        code: 待检查代码 (必填)
        language: "python"|"json"|"yaml"|"sql" (默认 python)

    返回 data: {"language", "valid": bool, "errors": [{line, col, message}], "summary"}
    """
    code = payload.get("code")
    if not isinstance(code, str):
        return {"code": "PARAM_MISSING", "message": "code 必须是字符串", "data": None, "retryable": False}
    if len(code) > 200_000:
        return {"code": "PARAM_INVALID", "message": "code 过长 (>200K)", "data": None, "retryable": False}

    lang = str(payload.get("language", "python") or "python").lower()
    errors: List[Dict[str, Any]] = []

    if lang in ("python", "py"):
        try:
            ast.parse(code, mode="exec")
        except SyntaxError as e:
            errors.append({
                "line": e.lineno or 0,
                "col": e.offset or 0,
                "message": e.msg or "syntax error",
            })

    elif lang == "json":
        try:
            json.loads(code)
        except json.JSONDecodeError as e:
            errors.append({
                "line": e.lineno,
                "col": e.colno,
                "message": e.msg,
            })

    elif lang in ("yaml", "yml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            return {
                "code": "TOOL_CALL_FAILED",
                "message": "yaml language 需要 PyYAML (pip install pyyaml)",
                "data": None, "retryable": False,
            }
        try:
            yaml.safe_load(code)
        except yaml.YAMLError as e:  # type: ignore[attr-defined]
            mark = getattr(e, "problem_mark", None)
            errors.append({
                "line": (mark.line + 1) if mark else 0,
                "col": (mark.column + 1) if mark else 0,
                "message": str(getattr(e, "problem", e)),
            })

    elif lang == "sql":
        # sqlite3 EXPLAIN — 不真正建表, 只编译 SQL 语句, 抓语法错
        import sqlite3
        try:
            with sqlite3.connect(":memory:") as conn:
                conn.execute(f"EXPLAIN {code}")
        except sqlite3.Error as e:
            errors.append({"line": 0, "col": 0, "message": str(e)})

    else:
        return {
            "code": "PARAM_INVALID",
            "message": f"不支持的 language: {lang} (允许: python/json/yaml/sql)",
            "data": None, "retryable": False,
        }

    valid = len(errors) == 0
    return {
        "code": "SUCCESS", "message": "ok",
        "data": {
            "language": lang,
            "valid": valid,
            "errors": errors,
            "summary": "代码语法合法" if valid else f"{len(errors)} 处语法错误",
        },
        "retryable": False,
    }


