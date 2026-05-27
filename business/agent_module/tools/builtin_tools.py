# -*- coding: utf-8 -*-
"""
Agent 内置扩展工具集合 (Task #37)

每个工具签名: Callable[[Dict[str, Any]], Dict[str, Any]]
统一返回信封: {"code": "SUCCESS"|"<ERR>", "message": str, "data": dict|null, ...}

工具列表:
    calculator_tool       — AST 安全数学求值, 无外部依赖
    datetime_tool         — 当前时间 / 时区算术
    wikipedia_tool        — Wikipedia REST API (urllib, 离线时降级 stub)
    make_document_read_tool(doc_store_factory)
                          — 工厂模式生成 callable, 按 doc_id 拉文档全文

TOOL_DESCRIPTIONS 是给 SimpleAgent._build_planner_prompt 用的 docstring 表;
bootstrap 在 register 时也会把对应 description 传给 DictToolRegistry。
"""

from __future__ import annotations

import ast
import json
import math
import operator
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional


# ============================================================
# 1. calculator_tool — AST 安全数学求值
# ============================================================

_ALLOWED_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_ALLOWED_FUNCS = {
    "sqrt": math.sqrt, "pow": math.pow, "abs": abs, "round": round,
    "floor": math.floor, "ceil": math.ceil,
    "log": math.log, "log10": math.log10, "log2": math.log2,
    "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "pi": lambda: math.pi, "e": lambda: math.e,
    "max": max, "min": min, "sum": sum,
}
_ALLOWED_CONSTS = {"pi": math.pi, "e": math.e, "inf": math.inf}


def _safe_eval(node: ast.AST) -> Any:
    """递归求值 AST 节点, 只允许预设运算符 + 数字 + 调用白名单函数."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):  # numbers / bool
        if isinstance(node.value, (int, float, bool)):
            return node.value
        raise ValueError(f"不支持的常量类型: {type(node.value).__name__}")
    if isinstance(node, ast.BinOp):
        op_cls = type(node.op)
        if op_cls not in _ALLOWED_BIN_OPS:
            raise ValueError(f"不支持的二元运算符: {op_cls.__name__}")
        return _ALLOWED_BIN_OPS[op_cls](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op_cls = type(node.op)
        if op_cls not in _ALLOWED_UNARY_OPS:
            raise ValueError(f"不支持的一元运算符: {op_cls.__name__}")
        return _ALLOWED_UNARY_OPS[op_cls](_safe_eval(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_CONSTS:
            return _ALLOWED_CONSTS[node.id]
        raise ValueError(f"未知标识符: {node.id}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise ValueError(f"不允许调用: {getattr(node.func, 'id', node.func)!r}")
        fn = _ALLOWED_FUNCS[node.func.id]
        args = [_safe_eval(a) for a in node.args]
        return fn(*args) if args else fn()
    if isinstance(node, ast.List) or isinstance(node, ast.Tuple):
        return [_safe_eval(e) for e in node.elts]
    raise ValueError(f"不支持的 AST 节点: {type(node).__name__}")


def calculator_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
    """安全数学计算. payload: {"expression": "1+2*sqrt(9)+pi"}

    支持: + - * / // % ** 一元正负, 函数 sqrt/pow/abs/round/floor/ceil/log*/exp/三角/max/min/sum,
          常量 pi/e/inf。
    禁止: 任何变量名解引用, 属性访问, 字符串, 切片, lambda, import, ...
    """
    expr = str(payload.get("expression") or "").strip()
    if not expr:
        return {
            "code": "PARAM_MISSING", "message": "expression 不能为空", "data": None,
            "retryable": False,
        }
    if len(expr) > 200:
        return {
            "code": "PARAM_INVALID", "message": "expression 过长 (>200)", "data": None,
            "retryable": False,
        }
    try:
        tree = ast.parse(expr, mode="eval")
        result = _safe_eval(tree)
    except SyntaxError as e:
        return {
            "code": "PARAM_INVALID", "message": f"语法错误: {e}", "data": None,
            "retryable": False,
        }
    except Exception as e:
        return {
            "code": "TOOL_CALL_FAILED", "message": str(e), "data": None,
            "retryable": False,
        }
    return {
        "code": "SUCCESS",
        "message": "ok",
        "data": {"expression": expr, "result": result},
        "retryable": False,
    }


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


# ============================================================
# 4. document_read_tool — 工厂模式, 闭包 doc_store
# ============================================================

def make_document_read_tool(
    doc_store_factory: Callable[[str], Any],
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """工厂: 把 (按 tenant_id 构造 doc_store 的 callable) 闭包成一个工具.

    payload:
        {"doc_id": str, "tenant_id": str = "default", "max_chars": int = 2000}

    返回 data: {"doc_id", "file_name", "file_type", "total_chars", "content"}
    跨租户 / doc 不存在 -> DOCUMENT_NOT_FOUND (跟 §9.3 一致, 不区分)。
    """

    def _read(payload: Dict[str, Any]) -> Dict[str, Any]:
        doc_id = str(payload.get("doc_id") or "").strip()
        if not doc_id:
            return {"code": "PARAM_MISSING", "message": "doc_id 不能为空", "data": None, "retryable": False}
        tenant_id = str(payload.get("tenant_id") or "default")
        max_chars = max(100, min(int(payload.get("max_chars", 2000) or 2000), 20000))

        try:
            store = doc_store_factory(tenant_id)
        except Exception as e:
            return {"code": "TOOL_CALL_FAILED", "message": f"doc_store_factory 失败: {e}",
                    "data": None, "retryable": True}
        try:
            doc = store.get_document(doc_id)
        except ValueError as e:
            return {"code": "PARAM_INVALID", "message": str(e), "data": None, "retryable": False}
        except Exception as e:
            return {"code": "TOOL_CALL_FAILED", "message": str(e), "data": None, "retryable": True}

        if not doc:
            return {"code": "DOCUMENT_NOT_FOUND", "message": "文档不存在",
                    "data": {"doc_id": doc_id}, "retryable": False}

        content = str(doc.get("content") or "")
        truncated = content[:max_chars]
        return {
            "code": "SUCCESS", "message": "ok",
            "data": {
                "doc_id": doc_id,
                "file_name": doc.get("file_name"),
                "file_type": doc.get("file_type"),
                "total_chars": len(content),
                "content": truncated,
                "truncated": len(content) > max_chars,
            },
            "retryable": False,
        }

    return _read


# ============================================================
# 工具描述表 — 给 Agent LLM 看, 决定何时调用何工具
# ============================================================

TOOL_DESCRIPTIONS: Dict[str, str] = {
    "calculator": (
        "安全数学计算, 不联网。"
        ' input: {"expression": str}. 支持 + - * / // % ** sqrt pow log exp 三角函数 + 常量 pi/e。'
        " 适合: 算术、统计、单位换算。"
    ),
    "datetime": (
        "时间工具, 不联网。"
        ' input: {"op": "now"|"add"|"diff", ...}.'
        ' op=now (可选 tz_offset_hours), op=add (iso + days/hours/...), op=diff (iso_start + iso_end).'
        " 适合: 当前时间、日期算术、距某天还有几天。"
    ),
    "wikipedia": (
        "Wikipedia 摘要查询, 需联网。"
        ' input: {"query": str, "lang": "zh"|"en", "max_chars": int}.'
        " 适合: 查询人物/概念/历史事件的权威信息。无网时会失败, 应降级到 llm_generate。"
    ),
    "document_read": (
        "按 doc_id 拉本地文档的全文 (从 document_store)。"
        ' input: {"doc_id": str, "tenant_id": str = "default", "max_chars": int = 2000}.'
        " 适合: rag_search 命中后想看完整原文, 或 citations 钻取。"
    ),
}
