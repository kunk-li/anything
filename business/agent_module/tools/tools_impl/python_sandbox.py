# -*- coding: utf-8 -*-
"""
Tool: python_sandbox (Task MM #73)
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
# 15. python_sandbox — AST 严格白名单 + 超时
# ============================================================
# ⚠️ 关键约束 (Python sandbox 是公认难题, 这是教学/受限版本, 不能跑用户脚本):
#   - 仅允许下面列出的 AST 节点类型 + 内置函数白名单
#   - 禁止: import, attribute access, exec, eval, open, __ 双下划线, 函数定义, 类定义
#   - 超时通过 threading.Timer + 协作式停止 (Python 没法强杀线程, 极端死循环可能 hang)

_SANDBOX_ALLOWED_AST_NODES = {
    ast.Module, ast.Expression, ast.Expr,
    ast.Constant, ast.Name, ast.Load, ast.Store,
    ast.BinOp, ast.UnaryOp, ast.BoolOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.UAdd, ast.USub, ast.Not,
    ast.And, ast.Or,
    ast.Compare,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.IfExp,                                 # 三元
    ast.Call,
    ast.List, ast.Tuple, ast.Set, ast.Dict,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
    ast.comprehension,
    ast.Subscript, ast.Slice, ast.Index if hasattr(ast, "Index") else ast.Slice,
    ast.Assign, ast.AugAssign,
    ast.If, ast.For, ast.While, ast.Pass, ast.Break, ast.Continue,
    ast.Return,
    ast.JoinedStr, ast.FormattedValue,        # f-string
    ast.Starred,
    ast.keyword,
}

_SANDBOX_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool,
    "dict": dict, "enumerate": enumerate, "filter": filter, "float": float,
    "int": int, "len": len, "list": list, "map": map,
    "max": max, "min": min, "range": range, "round": round,
    "set": set, "sorted": sorted, "str": str, "sum": sum,
    "tuple": tuple, "zip": zip, "reversed": reversed,
    "True": True, "False": False, "None": None,
    # 数学
    "math_pi": math.pi, "math_e": math.e,
}


def _sandbox_validate_ast(tree: ast.AST) -> Optional[str]:
    """遍历 AST, 任一节点不在白名单 -> 返回错误描述; 通过返回 None."""
    for node in ast.walk(tree):
        if type(node) not in _SANDBOX_ALLOWED_AST_NODES:
            return f"禁止的 AST 节点: {type(node).__name__}"
        if isinstance(node, ast.Name):
            if node.id.startswith("__") and node.id.endswith("__"):
                return f"禁止访问 __ 双下划线名称: {node.id}"
        if isinstance(node, ast.Attribute):
            return "禁止属性访问 (xxx.yyy)"
        if isinstance(node, ast.Call):
            # Call 的 func 必须是 Name 且在白名单
            if not isinstance(node.func, ast.Name):
                return "禁止调用非简单函数名"
            if node.func.id not in _SANDBOX_BUILTINS:
                return f"禁止调用未在白名单的函数: {node.func.id}"
    return None


def python_sandbox(payload: Dict[str, Any]) -> Dict[str, Any]:
    """超受限 Python 执行. 比 calculator 更宽 (有 for / if / 列表推导 / dict / 切片),
    但仍严禁 import / attribute / 双下划线 / 函数定义 / 类定义。

    payload:
        code: 待执行代码 (必填)
        timeout_seconds: 默认 2.0, 上限 10.0

    返回 data: {"stdout": str, "result": Any, "elapsed_seconds": float}
        - "result": 最后一个表达式的值 (如果是赋值语句序列, 取 `result` 变量, 没有则 None)
        - "stdout": 暂不支持 (sandbox 不允许 print)

    ⚠️ 教学/受限实现, 不能用来跑不可信代码 — Python sandbox 是公认难题,
       生产部署建议跑在隔离进程 / Docker / gVisor 里。
    """
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        return {"code": "PARAM_MISSING", "message": "code 不能为空", "data": None, "retryable": False}
    if len(code) > 5000:
        return {"code": "PARAM_INVALID", "message": "code 过长 (>5000)", "data": None, "retryable": False}

    timeout_s = max(0.1, min(float(payload.get("timeout_seconds", 2.0) or 2.0), 10.0))

    # 1. 解析 + AST 白名单校验
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return {"code": "PARAM_INVALID", "message": f"语法错误: {e}", "data": None, "retryable": False}
    err = _sandbox_validate_ast(tree)
    if err:
        return {"code": "PARAM_INVALID", "message": err, "data": None, "retryable": False}

    # 2. 在受限命名空间执行, 用线程 + 标志位做协作式超时
    import threading
    import time as _time
    result_holder: Dict[str, Any] = {"value": None, "error": None}
    safe_globals = {"__builtins__": {}}
    safe_globals.update(_SANDBOX_BUILTINS)
    safe_locals: Dict[str, Any] = {}

    def _run():
        try:
            exec(compile(tree, "<sandbox>", "exec"), safe_globals, safe_locals)
        except Exception as e:
            result_holder["error"] = f"{type(e).__name__}: {e}"

    thread = threading.Thread(target=_run, daemon=True)
    t0 = _time.time()
    thread.start()
    thread.join(timeout=timeout_s)
    elapsed = _time.time() - t0

    if thread.is_alive():
        # 没法强杀, 但标记 timeout (线程可能继续跑直到自然结束)
        return {
            "code": "TOOL_CALL_FAILED",
            "message": f"超时 (>{timeout_s}s)",
            "data": {"elapsed_seconds": round(elapsed, 3)},
            "retryable": False,
        }
    if result_holder["error"]:
        return {
            "code": "TOOL_CALL_FAILED",
            "message": result_holder["error"],
            "data": {"elapsed_seconds": round(elapsed, 3)},
            "retryable": False,
        }

    # 提取 `result` 变量 (如果用户定义了), 否则取最后一个赋值变量, 都没就 None
    final = safe_locals.get("result")
    if final is None and safe_locals:
        # 取最后赋值的非内部变量
        for k in list(safe_locals.keys())[::-1]:
            if not k.startswith("_"):
                final = safe_locals[k]
                break

    return {
        "code": "SUCCESS", "message": "ok",
        "data": {
            "result": final,
            "locals_keys": [k for k in safe_locals.keys() if not k.startswith("_")],
            "elapsed_seconds": round(elapsed, 3),
        },
        "retryable": False,
    }


