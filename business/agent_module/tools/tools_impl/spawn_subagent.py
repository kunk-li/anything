# -*- coding: utf-8 -*-
"""
Tool: spawn_subagent (Task MM #73)
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

def make_spawn_subagent_tool(parent_agent: Any) -> Callable[..., Dict[str, Any]]:
    """生成 spawn_subagent 工具闭包. parent_agent 是宿主 SimpleAgent 实例.

    子 agent 复用宿主的 tool_registry / llm_planner / state_store / deps,
    但通过 allowed_tools 限制可见工具集. ReAct 跑完后把 final_answer 包装成
    标准 tool 返回结构 (code/data/success).
    """
    def spawn_subagent(
        role: Optional[str] = None,
        task: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        max_iterations: Optional[int] = None,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        extra_params: Optional[Dict[str, Any]] = None,
        **_ignored,
    ) -> Dict[str, Any]:
        if not task:
            return {
                "code": "PARAM_MISSING",
                "message": "task 不能为空",
                "data": None,
                "success": False,
            }
        if parent_agent is None or parent_agent.tool_registry is None:
            return {
                "code": "SERVICE_UNAVAILABLE",
                "message": "spawn_subagent 需要 parent_agent 注入",
                "data": None,
                "success": False,
            }

        # 1. 解析子 agent 工具白名单 (取父 registry 的子集)
        parent_tools = _list_parent_tools(parent_agent.tool_registry)
        if allowed_tools:
            allowed = [t for t in allowed_tools if t in parent_tools]
        else:
            # 默认 inherit 父全部工具, 但去掉 spawn_subagent 自身防递归
            allowed = [t for t in parent_tools if t != "spawn_subagent"]
        if not allowed:
            return {
                "code": "PARAM_INVALID",
                "message": f"allowed_tools 跟父 registry 无交集. 父可见: {parent_tools[:10]}",
                "data": None,
                "success": False,
            }

        # 2. new 一个子 SimpleAgent (复用父的关键字段)
        try:
            # 延迟 import 避免循环
            from agent_module.core.impl import SimpleAgent as _SA
        except Exception:
            return {
                "code": "UNKNOWN_ERROR",
                "message": "无法导入 SimpleAgent",
                "data": None,
                "success": False,
            }

        child_registry = _RestrictedRegistry(parent_agent.tool_registry, allow=set(allowed))
        child_agent = _SA(
            state_store=parent_agent.state_store,
            tool_registry=child_registry,
            timeout=parent_agent.timeout,
            max_retries=parent_agent.max_retries,
            llm_planner=getattr(parent_agent, "llm_planner", None),
        )
        child_agent.execution_strategy = "react"
        child_agent.use_llm_planner = False  # role 是父决定的, 不让子重新规划
        # 不继承父的 approval 白名单 — 子 agent 只能用 allowed 里的工具,
        # 那些工具父已经审批过, 子运行期需要再过一遍是过度防御.
        child_agent.tool_approval_required = set()
        if max_iterations is not None:
            try:
                child_agent.max_react_iterations = max(1, min(10, int(max_iterations)))
            except (ValueError, TypeError):
                pass

        # 3. 跑子 task
        role_hint = f"[role={role}] " if role else ""
        sub_task = role_hint + str(task)
        # 子 trace_id 衍生自父, session_id 复用 (历史共享便于调试)
        child_trace = f"{trace_id or 'subagent'}#sub"
        child_session = session_id or "subagent_session"
        try:
            result = child_agent.execute({
                "task": sub_task,
                "trace_id": child_trace,
                "session_id": child_session,
                "extra_params": (extra_params or {}),
            })
        except Exception as e:
            return {
                "code": "UNKNOWN_ERROR",
                "message": f"子 agent 执行异常: {e}",
                "data": None,
                "success": False,
            }

        # 4. 整理回报给父 agent (保留 answer + steps 概要)
        data = result.get("data") or {}
        return {
            "code": result.get("code", "UNKNOWN_ERROR"),
            "message": result.get("message", ""),
            "success": result.get("code") == "SUCCESS",
            "data": {
                "answer": data.get("answer") or "",
                "iterations_used": data.get("iterations_used"),
                "tool_results_summary": (data.get("tool_results_summary") or [])[:5],
                "allowed_tools": allowed,
                "role": role,
            },
        }

    return spawn_subagent


def _list_parent_tools(registry: Any) -> List[str]:
    """从父 tool_registry 拿可见工具名列表. 兼容 DictToolRegistry / dict / 任意 list_tools()."""
    if registry is None:
        return []
    if hasattr(registry, "list_tools"):
        try:
            return list(registry.list_tools())
        except Exception:
            pass
    if isinstance(registry, dict):
        return list(registry.keys())
    return []


class _RestrictedRegistry:
    """子 agent 用的工具注册表代理: 把父 registry 包一层, 只暴露 allow 集合内的工具.

    支持 register / get / list_tools / describe / describe_all 4 个常用接口,
    跟 DictToolRegistry 协议兼容.
    """
    def __init__(self, parent: Any, allow: set):
        self._parent = parent
        self._allow = set(allow)

    def register(self, name: str, tool: Any, description: str = "") -> None:
        # 子 agent 想注册新工具? 拒绝 — 它的工具集是父定的
        return None

    def get(self, name: str) -> Any:
        if name not in self._allow:
            return None
        if hasattr(self._parent, "get"):
            return self._parent.get(name)
        if isinstance(self._parent, dict):
            return self._parent.get(name)
        return None

    def list_tools(self) -> List[str]:
        if hasattr(self._parent, "list_tools"):
            try:
                avail = set(self._parent.list_tools())
            except Exception:
                avail = set()
        elif isinstance(self._parent, dict):
            avail = set(self._parent.keys())
        else:
            avail = set()
        return sorted(self._allow & avail)

    def describe(self, name: str) -> str:
        if name not in self._allow:
            return ""
        if hasattr(self._parent, "describe"):
            try:
                return self._parent.describe(name)
            except Exception:
                return ""
        return ""

    def describe_all(self) -> Dict[str, str]:
        if hasattr(self._parent, "describe_all"):
            try:
                all_desc = self._parent.describe_all()
            except Exception:
                all_desc = {}
        else:
            all_desc = {}
        return {n: d for n, d in all_desc.items() if n in self._allow}


