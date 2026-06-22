# -*- coding: utf-8 -*-
"""
PlannerMixin (从 impl.py 拆出 — 任务规划 single_shot 路径, 零行为变更)

    _llm_plan_task        让 LLM 输出 JSON 执行计划; 任一环节失败返回 None 触发 fallback
    _rule_based_plan_task 规则式规划 (LLM 不可用时的 fallback)

注: 公开入口 parse_task 留在 impl.py —— 它是 BaseAgent 的 @abstractmethod 实现, 必须直接挂在
    SimpleAgent 上 (mixin 排在 BaseAgent 之后, 抽象方法会被 BaseAgent 的版本先解析到 → 无法实例化)。

依赖 SimpleAgent (self) 字段/方法 (由 __init__ 及其它 mixin / impl 提供):
    use_llm_planner, max_planner_steps, logger,
    _resolve_llm_planner (impl), _available_tool_names / _tool_descriptions (impl),
    _build_planner_prompt / _parse_planner_response (PromptBuilderMixin)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class PlannerMixin:
    """任务规划助手 (single_shot 路径): LLM 规划 + 规则式兜底。"""

    def _llm_plan_task(
            self,
            task: str,
            trace_id: Optional[str],
            extra_params: Dict[str, Any],
    ) -> Optional[List[Dict[str, Any]]]:
        """让 LLM 输出 JSON 格式的执行计划; 任一环节失败返回 None 触发 fallback."""
        llm_call = self._resolve_llm_planner(trace_id=trace_id)
        if llm_call is None:
            return None

        available_tools = self._available_tool_names()
        if not available_tools:
            return None

        prompt = self._build_planner_prompt(
            task=task, available_tools=available_tools,
            tool_descriptions=self._tool_descriptions(),
            project_root=extra_params.get("active_project_root"),
        )

        try:
            raw = llm_call(prompt)
        except Exception as e:
            self.logger.warning(f"[planner] LLM 规划调用异常, fallback 规则式: {e}")
            return None

        plan_steps = self._parse_planner_response(raw=raw, available_tools=available_tools)
        if not plan_steps:
            return None

        # 补全 step 字段
        steps: List[Dict[str, Any]] = []
        for i, step in enumerate(plan_steps[: self.max_planner_steps], start=1):
            raw_input_data = step.get("input_data")
            input_data = raw_input_data if isinstance(raw_input_data, dict) else {}
            input_data.setdefault("trace_id", trace_id)
            input_data.setdefault("extra_params", extra_params)
            if step.get("tool_name") == "rag_search":
                input_data.setdefault("top_k", extra_params.get("top_k", 5))
                input_data.setdefault("query", task)
            if step.get("tool_name") == "llm_generate":
                input_data.setdefault("prompt", task)
            steps.append({
                "step_id": step.get("step_id") or f"s{i}",
                "tool_name": step["tool_name"],
                "description": step.get("description", ""),
                "input_data": input_data,
            })

        self.logger.info(
            f"[planner] LLM 规划成功: trace_id={trace_id}, steps={len(steps)}, "
            f"tools={[s['tool_name'] for s in steps]}"
        )
        return steps

    def _rule_based_plan_task(
            self,
            task: str,
            execution_mode: str,
            trace_id: Optional[str],
            extra_params: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """规则式规划 (LLM 不可用时的 fallback)."""
        steps: List[Dict[str, Any]] = []

        if execution_mode == "hybrid":
            steps.append({
                "step_id": "s1", "tool_name": "rag_search",
                "description": "先做知识库检索",
                "input_data": {
                    "query": task, "top_k": extra_params.get("top_k", 5),
                    "trace_id": trace_id, "extra_params": extra_params,
                },
            })
            steps.append({
                "step_id": "s2", "tool_name": "llm_generate",
                "description": "基于检索结果生成总结",
                "input_data": {
                    "prompt": task, "trace_id": trace_id, "extra_params": extra_params,
                },
            })
        else:
            steps.append({
                "step_id": "s1", "tool_name": "llm_generate",
                "description": "执行通用文本生成",
                "input_data": {
                    "prompt": task, "trace_id": trace_id, "extra_params": extra_params,
                },
            })
        return steps
