# -*- coding: utf-8 -*-
"""
Tool: calculator (Task MM #73)
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


