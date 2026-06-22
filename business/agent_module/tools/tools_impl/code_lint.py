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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ============================================================
# 10. code_lint — 多语言语法检查
# ============================================================

_EXT_LANG = {"py": "python", "json": "json", "yaml": "yaml", "yml": "yaml", "sql": "sql"}


def _resolve_lint_path(raw: str) -> Optional[Path]:
    """把 code_lint 的 path 限制在项目源码树 (cwd) 内, 防任意文件读取.

    code_lint 的用途是审本项目源码 (见 docstring), 所以沙盒边界 = 项目根 (cwd),
    比 pdf/excel 的 uploads/ 白名单宽, 但仍拒绝绝对路径越界与 .. 穿越出根。
    返回归一化后的绝对 Path; 越界/不存在/非文件 → None。
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        root = Path.cwd().resolve()
        # 相对路径拼到 cwd; 绝对路径直接 resolve —— 两者都 resolve 后用 relative_to 校验
        candidate = (root / raw if not Path(raw).is_absolute() else Path(raw)).resolve(strict=False)
    except (OSError, ValueError):
        return None
    try:
        candidate.relative_to(root)  # 越出项目根 (含 .. 穿越/绝对路径逃逸) → ValueError
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def code_lint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """代码语法检查 (静态, 不执行).

    payload (code / path 二选一):
        code: 待检查的代码字符串
        path: 待检查文件的路径 —— **审查本项目源码请优先用 path**: 工具直接从磁盘读真实文件
              再检查, 避免把文件内容抄进 code 时截断/抄错导致**误报语法错** (只读, 不执行)。
        language: "python"|"json"|"yaml"|"sql" (默认 python; 给了 path 会按扩展名自动推断)

    返回 data: {"language", "valid": bool, "errors": [{line, col, message}], "summary", "source"}
    """
    code = payload.get("code")
    path = payload.get("path")
    inferred_lang = None
    source = "inline"
    # path 优先 (或 code 缺失时回退 path): 直接读真实文件, 不依赖模型复述 → 根治"审代码假语法错"。
    if isinstance(path, str) and path.strip() and not (isinstance(code, str) and code):
        safe = _resolve_lint_path(path)
        if safe is None:
            return {"code": "PARAM_INVALID",
                    "message": f"path 无效或越出项目源码树 (禁绝对路径/.. 穿越, 须为 cwd 内的文件): {path!r}",
                    "data": None, "retryable": False}
        try:
            with open(safe, "r", encoding="utf-8", errors="replace") as _f:
                code = _f.read()
        except OSError as e:
            return {"code": "TOOL_CALL_FAILED", "message": f"读取文件失败: {path} ({e})",
                    "data": None, "retryable": False}
        _ext = safe.suffix.lstrip(".").lower()
        inferred_lang = _EXT_LANG.get(_ext)
        source = f"path:{path}"

    if not isinstance(code, str):
        return {"code": "PARAM_MISSING", "message": "需要 code (代码字符串) 或 path (文件路径) 之一",
                "data": None, "retryable": False}
    if len(code) > 200_000:
        return {"code": "PARAM_INVALID", "message": "code 过长 (>200K)", "data": None, "retryable": False}

    lang = str(payload.get("language") or inferred_lang or "python").lower()
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
            "source": source,
        },
        "retryable": False,
    }


