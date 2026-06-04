# -*- coding: utf-8 -*-
"""
PromptBuilderMixin (Task KK #71)

集中所有 prompt 构造与 LLM 输出解析:
    _build_react_prompt          ReAct 循环每轮 prompt (含 ProjectMemory + Skills 注入)
    _build_planner_prompt        单次规划 prompt (single_shot 路径)
    _parse_planner_response      解析 LLM 输出的 step 列表

注: _parse_react_response 紧贴 _react_execute 用, 放在 ReActEngineMixin 里 (一起更内聚).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from common_utils_module import (
    get_project_memory,
    get_skill_registry,
    inject_skills_into_prompt,
)


class PromptBuilderMixin:
    """Prompt 构造 + 规划响应解析."""

    @staticmethod
    def _build_react_prompt(
            task: str,
            available_tools: List[str],
            history: List[Dict[str, Any]],
            iteration: int,
            max_iterations: int,
            tool_descriptions: Optional[Dict[str, str]] = None,
    ) -> str:
        """构造 ReAct prompt: 任务 + 工具 + 历史 + 期望输出格式.

        tool_descriptions 是从 registry.describe_all() 拿到的, 优先级最高;
        缺失时退回内置 tool_docs (向后兼容 rag_search / llm_generate).

        Task U (#55): 顶部注入 ProjectMemory (AGENTS.md / CLAUDE.md).
        Task AA (#61): 命中关键字时拼上对应 skill body.
        """
        tool_descriptions = tool_descriptions or {}
        fallback_docs = {
            "rag_search": '{"query": str, "top_k": int}',
            "llm_generate": '{"prompt": str}',
        }
        tool_lines = []
        for n in available_tools:
            desc = tool_descriptions.get(n) or fallback_docs.get(n, '{}')
            tool_lines.append(f"- {n}: {desc}")

        history_lines = []
        for i, h in enumerate(history, start=1):
            history_lines.append(f"Iteration {i}:")
            if h.get("thought"):
                history_lines.append(f"  Thought: {h['thought']}")
            if h.get("action"):
                a = h["action"]
                history_lines.append(f"  Action: {a.get('tool')}({a.get('input')})")
            if h.get("observation"):
                history_lines.append(f"  Observation: {h['observation'][:300]}")

        history_text = "\n".join(history_lines) if history_lines else "(尚无历史)"

        # Task U: 顶部拼项目记忆 (AGENTS.md / CLAUDE.md)
        memory_block = ""
        try:
            mem = get_project_memory().load()
            if mem:
                memory_block = (
                    f"<ProjectMemory>\n{mem.strip()}\n</ProjectMemory>\n\n"
                )
        except Exception:
            memory_block = ""

        # Task AA (#61): 命中的 skill body 拼到 prompt 顶部
        skills_block = ""
        try:
            matched = get_skill_registry().match(task or "")
            if matched:
                # 借用 inject_skills_into_prompt 拼好的格式, 但只截 <Skills>...</Skills> 段
                wrapped = inject_skills_into_prompt("__TASK_PH__", matched, max_skills=3)
                end = wrapped.find("</Skills>")
                if end != -1:
                    skills_block = wrapped[: end + len("</Skills>")] + "\n\n"
        except Exception:
            skills_block = ""

        return (
            f"{memory_block}"
            f"{skills_block}"
            f"你是一个有真实执行能力的智能助手 (Agent)。下面列出的工具都会真正执行操作"
            f"(查询/计算/搜索/访问网页/读写文件/运行代码/控制电脑等), 不是模拟也不是假设。\n"
            f"原则: ①优先用工具动手把任务做完, 不要回答'我做不到'/'我只是语言模型'/'请你自己去做'; "
            f"②需要知识或文档时调 rag_search; 缺事实就先调工具拿到再答; "
            f"③只有当工具确实都不适用时, 才用 final_answer 说明并给出力所能及的替代方案。\n"
            f"当前任务: {task}\n"
            f"\n"
            f"可用工具:\n" + "\n".join(tool_lines) + "\n"
            f"\n"
            f"历史:\n{history_text}\n"
            f"\n"
            f"这是第 {iteration}/{max_iterations} 轮。请输出下一步思考与动作。\n"
            f"如果已经可以给出最终答案,直接输出 final_answer 而不要再调工具。\n"
            f"\n"
            f"请只输出严格 JSON(不要解释/markdown 围栏),二选一格式:\n"
            f'{{"thought": "<推理>", "final_answer": "<最终回答>"}}\n'
            f'或\n'
            f'{{"thought": "<推理>", "action": {{"tool": "<工具名>", "input": {{...}}}}}}\n'
        )

    @staticmethod
    def _build_planner_prompt(
        task: str,
        available_tools: List[str],
        tool_descriptions: Optional[Dict[str, str]] = None,
    ) -> str:
        """构造 LLM 规划 prompt (single_shot 路径用).

        tool_descriptions: 从 registry 取的描述, 优先级最高;
                           缺失时 fall back 到内置 tool_docs.
        """
        tool_descriptions = tool_descriptions or {}
        fallback_docs = {
            "rag_search": "在知识库中检索相关文档片段。input: {\"query\": str, \"top_k\": int}",
            "llm_generate": "调用大语言模型生成文本。input: {\"prompt\": str}",
        }
        tool_lines = []
        for name in available_tools:
            doc = tool_descriptions.get(name) or fallback_docs.get(name, "(无描述)")
            tool_lines.append(f"- {name}: {doc}")

        return (
            "你是一个任务规划器。根据用户任务,从可用工具中选择需要按顺序调用的工具序列。\n"
            "\n"
            "可用工具:\n"
            + "\n".join(tool_lines)
            + "\n"
            "\n"
            "规划原则:\n"
            "- 如果任务需要查阅知识库后再回答,先 rag_search 再 llm_generate\n"
            "- 如果只是文本生成/创作/计划,直接 llm_generate\n"
            "- 步骤数不超过 3 步\n"
            "\n"
            f"用户任务: {task}\n"
            "\n"
            "请只输出严格 JSON(不要任何解释/markdown 围栏),格式:\n"
            "{\n"
            "  \"steps\": [\n"
            "    {\"step_id\": \"s1\", \"tool_name\": \"<工具名>\", \"description\": \"<理由>\", \"input_data\": {...}}\n"
            "  ]\n"
            "}\n"
        )

    @staticmethod
    def _parse_planner_response(raw: str, available_tools: List[str]) -> Optional[List[Dict[str, Any]]]:
        """从 LLM 返回中提取 JSON steps 列表,校验工具合法性."""
        if not raw or not isinstance(raw, str):
            return None

        # 尽量从文本中抠出 {...} JSON 块(模型可能加了 markdown 围栏)
        candidate = raw.strip()
        if candidate.startswith("```"):
            # 去掉 markdown 代码围栏
            candidate = re.sub(r"^```[a-zA-Z]*\n?", "", candidate)
            candidate = re.sub(r"```\s*$", "", candidate).strip()

        # 抓第一个 { 到匹配的 }
        match = re.search(r"\{[\s\S]*\}", candidate)
        if not match:
            return None

        try:
            parsed = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return None

        steps = parsed.get("steps") if isinstance(parsed, dict) else None
        if not isinstance(steps, list) or not steps:
            return None

        tool_set = set(available_tools)
        valid_steps: List[Dict[str, Any]] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            tool_name = step.get("tool_name")
            if tool_name not in tool_set:
                # LLM 调了不存在的工具,拒绝整个 plan
                return None
            valid_steps.append(step)

        return valid_steps or None
