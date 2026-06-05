# -*- coding: utf-8 -*-
"""
Tool: use_skill — 按名加载一条已知技能(skill)的完整操作指南。

借鉴 obra/superpowers 的"任务前先查相关技能、把技能当工作流"机制: prompt 里注入了
技能目录(名+描述), agent 遇到相关任务时调本工具把该技能的详细步骤/注意点拉进上下文再做。
与 trigger 自动注入互补 (描述型/superpowers 式技能无 trigger, 靠目录+本工具按需加载)。

只读、无副作用 → 不进审批白名单。registry 可注入 (测试不碰全局单例)。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional


def make_use_skill_tool(registry_getter: Optional[Callable] = None) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """构造 use_skill 工具。registry_getter 缺省用全局 get_skill_registry (测试可注入)。"""

    def _reg():
        if registry_getter is not None:
            return registry_getter()
        from skills_module.impl import get_skill_registry
        return get_skill_registry()

    def use_skill(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = payload or {}
        trace_id = payload.get("trace_id")
        name = str(payload.get("name") or payload.get("skill") or "").strip()
        if not name:
            return {"code": "PARAM_MISSING", "message": "use_skill 需 name (技能名, 取自技能目录)",
                    "data": None, "trace_id": trace_id, "retryable": False}
        try:
            reg = _reg()
            skill = reg.get_by_name(name)
        except Exception as e:
            return {"code": "TOOL_CALL_FAILED", "message": f"加载技能失败: {str(e)[:200]}",
                    "data": None, "trace_id": trace_id, "retryable": True}
        if skill is None:
            try:
                names = [c["name"] for c in reg.catalog()]
            except Exception:
                names = []
            return {"code": "SKILL_NOT_FOUND",
                    "message": f"无此技能: {name!r}。可用技能: {names[:20]}",
                    "data": None, "trace_id": trace_id, "retryable": False}
        return {"code": "SUCCESS", "message": "ok",
                "data": {"name": skill.name, "description": skill.description,
                         "tools": list(skill.tools), "body": skill.body},
                "trace_id": trace_id, "retryable": False}

    return use_skill


USE_SKILL_DESCRIPTION = (
    '加载一条已知技能(skill)的完整操作指南。遇到与"技能目录"里某项相关的任务时, 先调它把该技能的'
    '详细步骤/注意点/最佳实践拉进来, 再按指南动手 —— 别凭空做。'
    'input: {"name": "技能名(必须取自上面的技能目录)"}。返回该技能的 body 指南 + 关联工具。只读、无需审批。'
)
