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

        # Task AA (#61): 命中的 skill body 拼到 prompt 顶部 (trigger 自动注入)
        matched = []
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

        # superpowers 式技能目录: 列出全部技能(名+描述), 让 agent 知道有哪些技能可用 —— 遇到
        # 相关任务时先调 use_skill(name) 把该技能完整指南拉进来再做(模型驱动, 补 trigger 自动注入)。
        catalog_block = ""
        try:
            # 排除已 trigger 自动注入的技能 (body 已在上面 skills_block), 免得 agent 又 use_skill
            # 白跑一轮 (省一次 LLM 调用, 治延迟)。目录只列"没自动注入、需要时才按需加载"的技能。
            _injected = {s.name for s in matched}
            cat = [c for c in get_skill_registry().catalog() if c["name"] not in _injected]
            if cat:
                lines = "\n".join(f"- {c['name']}: {(c.get('description') or '')[:80]}"
                                  for c in cat[:40])
                catalog_block = (
                    "\n可用技能(skill)目录 — 遇到与下面某条技能相关的任务时, 先调 use_skill(name) "
                    "加载该技能的完整指南再动手:\n" + lines + "\n"
                )
        except Exception:
            catalog_block = ""

        # 缓存友好结构 (#2): [全局稳定前缀] → [任务块] → [易变块]。
        # 稳定前缀(项目记忆+赋能前言+工具表+输出格式)跨所有 agent 调用一致 → 命中 provider 的
        # prefix cache(qwen/OpenAI 自动按 token 前缀缓存) → 降 token 成本 + 缩短 TTFT。
        # 原先把 task 夹在工具表前, 导致工具表(最大的稳定块)被 task 切断、无法跨任务复用缓存。
        stable_prefix = (
            f"{memory_block}"
            f"你是一个有真实执行能力的智能助手 (Agent)。下面列出的工具都会真正执行操作"
            f"(查询/计算/搜索/访问网页/读写文件/运行代码/看本机状态等), 不是模拟也不是假设。\n"
            f"用工具 vs 直接答 的判断原则:\n"
            f"①能用你自己的知识直接回答的 (规划/建议/方案/解释/写作/分析/常识等), 就直接给 final_answer, "
            f"不要为这类问题去搜知识库或外网 —— 你本来就会, 搜了反而慢还可能失败;\n"
            f"②只有在确实需要[外部/实时数据]或[对本机的操作]时才调工具: 实时/最新资讯→web_search; "
            f"精确算数→calculator; 本机状态(CPU/内存/磁盘/进程/系统)→system_info; 查用户已上传的文档→rag_search; "
            f"读写文件/运行代码/控制桌面→对应工具;\n"
            f"③本机相关 (这台电脑/系统使用情况) 必须调 system_info 读真实数据再答, 绝不可说'我无法访问你的电脑'(你能, 就靠工具);\n"
            f"④绝不要回答'我做不到'/'我只是语言模型'/'请你自己去弄' —— 能直接答就答, 该用工具就用;\n"
            f"⑤工具失败或无结果 (如无网、知识库为空) 时, 改用你自己的知识把问题尽力答完, 不要卡住、也不要反复重试同一个失败的工具。\n"
            f"\n可用工具:\n" + "\n".join(tool_lines) + "\n"
            f"{catalog_block}"
            f"\n请只输出严格 JSON(不要解释/markdown 围栏),三选一格式:\n"
            f'{{"thought": "<推理>", "final_answer": "<最终回答>"}}\n'
            f'或 (调用单个工具):\n'
            f'{{"thought": "<推理>", "action": {{"tool": "<工具名>", "input": {{...}}}}}}\n'
            f'或 (多个相互独立的工具一步并发执行, 仅当它们彼此不依赖时用, 省时间):\n'
            f'{{"thought": "<推理>", "actions": [{{"tool": "<工具A>", "input": {{...}}}}, {{"tool": "<工具B>", "input": {{...}}}}]}}\n'
        )
        return (
            stable_prefix
            + f"\n{skills_block}当前任务: {task}\n"
            + f"\n历史:\n{history_text}\n"
            + f"\n这是第 {iteration}/{max_iterations} 轮。请输出下一步思考与动作。\n"
            + f"如果已经可以给出最终答案,直接输出 final_answer 而不要再调工具。\n"
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
