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
from pathlib import Path
from typing import Any, Dict, List, Optional

from common_utils_module import (
    get_project_memory,
    load_project_memory_for_root,
    get_skill_registry,
    inject_skills_into_prompt,
)

# 项目根目录: 本文件在 business/agent_module/core/components/ 下, 上溯 5 级 (parents[4]) 到仓库根
# (与 impl.py._resolve_project_root 同源; _build_react_prompt 是 @staticmethod 无 self, 故就地解析)。
# 注入 ReAct 提示词原则⑧, 让 agent 知道"本项目源码在哪、用绝对路径读" —— 根治"审代码靠记忆编造"。
try:
    _PROJECT_ROOT = str(Path(__file__).resolve().parents[4])
except Exception:
    _PROJECT_ROOT = "(无法解析项目根)"


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
            project_root: Optional[str] = None,
            match_text: Optional[str] = None,
    ) -> str:
        """构造 ReAct prompt: 任务 + 工具 + 历史 + 期望输出格式.

        tool_descriptions 是从 registry.describe_all() 拿到的, 优先级最高;
        缺失时退回内置 tool_docs (向后兼容 rag_search / llm_generate).

        Task U (#55): 顶部注入 ProjectMemory (AGENTS.md / CLAUDE.md).
        Task AA (#61): 命中关键字时拼上对应 skill body.

        match_text: 用于 skill trigger 匹配的文本; 默认 = task。调用方应传"用户原始问题"
        (未注入记忆/画像/历史/附件的原话), 否则注入进 task 的那些文本会误触发或抑制 skill 命中
        (trigger 是子串匹配, 一条画像 fact 里出现 trigger 词就会假命中)。不传时回退 task 保持向后兼容。
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
        _n_hist = len(history)
        for i, h in enumerate(history, start=1):
            history_lines.append(f"Iteration {i}:")
            if h.get("thought"):
                history_lines.append(f"  Thought: {h['thought']}")
            if h.get("action"):
                a = h["action"]
                history_lines.append(f"  Action: {a.get('tool')}({a.get('input')})")
            if h.get("observation"):
                # 最近一轮 observation 给足额度 (≤12000, 与 _summarize_tool_output 的 shell 输出上限一致),
                # 让模型据"当前完整结果"作答; 更早的压到 300 防 context 膨胀。
                # 早期若一律压 300, 会把"列清单/查库"类长输出切成半截 JSON, 诱导模型以为
                # 没查全而重复执行同一只读命令 → 死循环, 故最近一条不截短。
                # 12000(≈300 行源码): 审代码/读文件需看到整文件, 旧的 2000 会把文件腰斩 →
                # 模型据残文误报"不完整/某符号未定义"(实测 llm_compat.py 被切到第 68 行就误判)。
                _obs_cap = 12000 if i == _n_hist else 300
                history_lines.append(f"  Observation: {h['observation'][:_obs_cap]}")

        history_text = "\n".join(history_lines) if history_lines else "(尚无历史)"

        # 多项目: 当前项目作用域根 (来自请求 active_project_root); 不传则回退本平台自身根 _PROJECT_ROOT。
        # 用于: ProjectMemory 按该根加载 + 原则⑧ 告诉 agent "当前项目根在哪、读码用绝对路径打这里"。
        effective_root = project_root or _PROJECT_ROOT

        # 工作区说明 (原则⑧用): 选了项目时 shell_exec 的 CWD 已被设成该项目根 (见 shell_exec),
        # 产物按约定写到 outputs/ 子目录; 没选项目 (挂 Anything) 时 CWD 在 run/ 下。
        if project_root:
            _ws_line = (
                f"当前项目根目录: {effective_root} —— 这是本对话的**工作区**, shell_exec 命令就在这个目录下执行。"
                f"读该项目文件用相对或绝对路径都行; **生成的文件/报告/产物一律写到 outputs\\ 子目录** "
                f"(相对路径写 `outputs\\文件名`, 目录不存在先 `mkdir outputs`), 别散落到别处、也别和源码混在根目录。\n"
            )
        else:
            _ws_line = (
                f"当前项目根目录: {effective_root} (未指定具体项目; shell_exec 工作目录在 run\\ 下 —— "
                f"读项目文件请用绝对路径打到上面这个根下)。\n"
            )

        # Task U: 顶部拼项目记忆 (当前项目的 AGENTS.md / CLAUDE.md)。多项目: 按 effective_root 加载,
        # 选了别的项目就注入那个项目的说明; 没选 (project_root=None) 回退全局单例 (= 本平台自身)。
        memory_block = ""
        try:
            mem = load_project_memory_for_root(project_root)
            if mem:
                memory_block = (
                    f"<ProjectMemory>\n{mem.strip()}\n</ProjectMemory>\n\n"
                )
        except Exception:
            memory_block = ""

        # Task AA (#61): 命中的 skill body 拼到 prompt 顶部 (trigger 自动注入)
        # 用 match_text(= 用户原始问题) 匹配, 不用已注入记忆/画像/历史/附件的 task,
        # 否则注入文本会改变 trigger 命中 (子串匹配); 不传时回退 task。
        _match_text = match_text if match_text is not None else task
        matched = []
        skills_block = ""
        try:
            matched = get_skill_registry().match(_match_text or "")
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
            # 仅当"没有技能被 trigger 自动注入"时才展示技能目录: 让 agent 发现并按需 use_skill。
            # 若已有相关技能自动注入 (body 在上面 skills_block), 说明手头已有方法论, 此时再列目录
            # 只会诱导 agent 多调一次 use_skill 去加载其它技能 —— 实测对同类任务无质量增益 (答案反而
            # 更短) 却平白 +20~25s。故有命中就抑制目录, 让它直接用已注入的技能作答 (治延迟尖峰)。
            if not matched:
                cat = get_skill_registry().catalog()
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
            f"①纯靠你自己知识就能回答的[通用]问题 (通用规划/建议/方案/写作/常识、对通用概念的解释等), "
            f"直接给 final_answer, 不要去搜知识库或外网 —— 你本来就会, 搜了反而慢还可能失败; "
            f"但凡问题涉及[某个具体文件/源代码/目录结构/配置/真实数据]的内容 (尤其'看看/检查/审查/分析"
            f"本项目代码'这类), **不属于这一类** —— 必须先按下面⑧用工具读到真实内容再答, 绝不能凭记忆直接编;\n"
            f"②只有在确实需要[外部/实时数据]或[对本机的操作]时才调工具: 实时/最新资讯→web_search; "
            f"精确算数→calculator; 本机状态(CPU/内存/磁盘/进程/系统)→system_info; 查用户已上传的文档→rag_search; "
            f"读写文件/运行代码/控制桌面→对应工具;\n"
            f"③本机相关 (这台电脑/系统使用情况) 必须调 system_info 读真实数据再答, 绝不可说'我无法访问你的电脑'(你能, 就靠工具);\n"
            f"④绝不要回答'我做不到'/'我只是语言模型'/'请你自己去弄' —— 能直接答就答, 该用工具就用;\n"
            f"⑤工具失败或无结果 (如无网、知识库为空) 时, 改用你自己的知识把问题尽力答完, 不要卡住、也不要反复重试同一个失败的工具。\n"
            f"⑥涉及[数据/状态查询](数据库/文件/接口/本机状态等)时, **绝不要复用更早对话/会话里出现过的旧查询结论**"
            f"(数据会变、旧结论可能过时或本就查错), 必须在本次任务里**重新执行**对应命令/工具、拿到当前真实结果再答; "
            f"更早历史里说'空/没有/0 条/失败'时尤其要亲自重查一遍 —— 注意这指的是**更早的**旧结论。\n"
            f"⑦但**一旦你在本次任务的前几轮已经执行过某命令并拿到结果**(见下方历史的 Observation), 那就是当前真实结果, **直接据此作答**; "
            f"尤其**不要因为输出看起来被截断/不完整就重复执行同一条只读命令** —— 同一命令重复执行结果完全相同, "
            f"截断只是显示长度限制, 重跑拿不到更多, 只会空转死循环。\n"
            f"⑧**事实落地(防编造)**: 凡要陈述/引用/审查/定位[文件内容、源代码、函数/类/变量、配置、目录结构、真实数据、行号、bug]时, "
            f"**必须先用工具读到真实内容再据此作答**; 严禁凭记忆复述或编造代码/路径/函数名/行号/bug —— 读不到就如实说'没读到', 绝不猜。"
            f"读本项目或本机的源码与文件用 shell_exec: 看内容 `type <绝对路径>`(Linux 用 cat/Get-Content)、"
            f"全局找定义或用法 `findstr /s /n \"关键字\" <项目根>\\*.py`(或 `grep -rn`); 查用户上传的文档才用 rag_search/document_read。"
            f"{_ws_line}"
            f"⑨**分析/审查整个项目或代码库时**(不是单个文件): 不能只列目录名就照名字猜 —— "
            f"'application 可能是核心逻辑'这种是**猜**, 不是分析。要真正读代码再下结论, 按这个顺序: "
            f"(a) 先看结构: `dir /s /b <项目根>` 或读 AGENTS.md/README/CLAUDE.md; "
            f"(b) 再**实际读**关键文件的内容(入口/启动文件、各主要模块的 __init__ 或代表性 impl.py), "
            f"用 type/findstr 把它们读出来; (c) 据**你这次真正读到的代码**说清每个模块到底做什么、怎么组织、有什么值得注意的实现。"
            f"**结论与建议必须出自你读到的代码**, 严禁堆砌'加日志/提高测试覆盖率/检查依赖安全/加强多租户隔离'这类"
            f"放之四海皆准的通用清单 —— 那不是对**本**项目的分析。时间/篇幅有限就挑最关键的几个文件读深, "
            f"并说明你读了哪些、哪些还没覆盖, 而不是用通用套话凑数。\n"
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
            + "如果已经可以给出最终答案,直接输出 final_answer 而不要再调工具。\n"
        )

    @staticmethod
    def _build_planner_prompt(
        task: str,
        available_tools: List[str],
        tool_descriptions: Optional[Dict[str, str]] = None,
        project_root: Optional[str] = None,
    ) -> str:
        """构造 LLM 规划 prompt (single_shot 路径用).

        tool_descriptions: 从 registry 取的描述, 优先级最高;
                           缺失时 fall back 到内置 tool_docs.
        project_root: 当前项目作用域根 (多项目); 不传回退本平台自身根。
        """
        tool_descriptions = tool_descriptions or {}
        # 多项目: 读文件示例路径打到"当前项目根"下 (不传则本平台自身根)。
        effective_read_example = str(Path(project_root or _PROJECT_ROOT) / "目标文件.py")
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
            "- 涉及[具体文件/源代码/目录结构/真实数据]内容时 (如检查/审查/分析本项目代码),"
            " **必须先用 shell_exec 读到真实文件内容再分析**, 严禁凭记忆编造代码/路径/bug;"
            f" 读当前项目源码用绝对路径(不要用占位符), 例 input_data={{\"command\": \"type {effective_read_example}\"}} (Windows) 或 cat(Linux)\n"
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
