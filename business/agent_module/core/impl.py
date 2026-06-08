# -*- coding: utf-8 -*-
"""
Agent 模块具体实现类
负责任务解析、工具调用、状态记录与结果聚合

Task KK (#71): 内部拆 4 个 mixin (ReAct / ToolExecutor / Streaming / PromptBuilder)
   - 公共 API (execute/parse_task/run_stream/register_tool/...) 不变
   - 测试 0 改动
   - 见 components/ 下的 4 个 mixin 文件
"""

import os
import threading
import time
import uuid
from collections import OrderedDict
from typing import Dict, Any, List, Optional, Callable

from .base import BaseAgent
from .components import (
    ReActEngineMixin,
    ToolExecutorMixin,
    StreamingMixin,
    PromptBuilderMixin,
    SelfMaintenanceMixin,
    MemoryMixin,
)
from .components.verifier import collect_specs, make_registry, run_verifiers

from deps_module import BasicDeps, build_basic_deps, handle_exception_to_envelope
from observability_module import trace_span


class SimpleAgent(
    BaseAgent,
    ReActEngineMixin,
    ToolExecutorMixin,
    StreamingMixin,
    PromptBuilderMixin,
    SelfMaintenanceMixin,
    MemoryMixin,
):
    """标准 Agent 实现: 任务解析 -> 工具调用 -> 状态记录 -> 结果聚合.

    任务解析(parse_task)优先使用 LLM 规划:
        - 通过 LLM 让模型自己决定调用工具序列, 符合"智能代理"语义
        - 任一失败(LLM 不可用 / JSON 解析失败 / 工具不在注册表) → fallback 到规则式
        - 通过配置 agent.use_llm_planner=False 可强制关闭 LLM 规划

    内部能力 (通过 mixin 提供):
        ReActEngineMixin    多轮 ReAct 循环 + Plan mode 早出 (Task V)
        ToolExecutorMixin   工具调用 + LRU 缓存 (Task FF) + 审批门槛 (Task W)
        StreamingMixin      run_stream 流式 generator (Task #48)
        PromptBuilderMixin  ReAct prompt + planner prompt 构造
    """

    def __init__(
            self,
            state_store=None,
            tool_registry=None,
            timeout: int = 60,
            max_retries: int = 2,
            session_prefix: str = "session",
            llm_planner: Optional[Callable[[str], str]] = None,
            deps: Optional[BasicDeps] = None,
            long_term_memory=None,  # Task FFF (#92): 注入 LongTermMemoryImpl, None 时关闭
            llm_client=None,  # 真流式: 有 chat_stream 的 LLMService (跟 RAG 同一个)
    ):
        # 基础依赖优先走 DI 注入; 未注入时构造一套(向后兼容)
        deps = deps or build_basic_deps()
        self.utils = deps.utils
        self.logger = deps.logger
        self.config = deps.config
        self.exception_handler = deps.exception_handler
        # Task YY (#85): 暴露 deps 给 mixin 走 DI 拿 cross-cutting registries
        # (deps.hook_registry / deps.audit_logger / 等). mixin 通过 self.deps
        # 优先访问; None 时 fallback get_X() 全局单例.
        self.deps = deps

        # Task FFF (#92): 长期记忆 — 跨 session 持久化, 对话前注入相关 fact,
        # 对话后抽取新 fact 落盘. None 时所有 memory 路径都 no-op.
        self.long_term_memory = long_term_memory
        self.memory_top_k = self.config.get_effective_value(
            "agent.memory_top_k", env_var="ANYTHING_AGENT_MEMORY_TOP_K",
            default=5, value_type=int,
        )
        self.memory_enabled = bool(long_term_memory is not None and self.config.get_effective_value(
            "agent.memory_enabled", env_var="ANYTHING_AGENT_MEMORY_ENABLED",
            default=True, value_type=bool,
        ))

        # LLM 规划器: 可显式注入 callable (prompt -> str);
        # 未注入时回退到 tool_registry["llm_generate"]
        self.llm_planner = llm_planner
        # 真流式直连 LLMService (有 chat_stream). RAG 用同一个对象做 token 流;
        # Agent run_stream 默认直接 chat_stream 真 token 流 (TTFT 快, 逐字).
        self.llm_client = llm_client

        self.state_store = state_store
        self.tool_registry = tool_registry
        # 关键配置项走 get_effective_value, 允许环境变量覆盖
        self.timeout = self.config.get_effective_value(
            "agent.timeout", env_var="ANYTHING_AGENT_TIMEOUT",
            default=timeout, value_type=int,
        )
        self.max_retries = self.config.get_effective_value(
            "agent.max_retries", env_var="ANYTHING_AGENT_MAX_RETRIES",
            default=max_retries, value_type=int,
        )
        self.session_prefix = self.config.get_config("agent.session_prefix", session_prefix)
        self.default_execution_mode = self.config.get_config("agent.default_execution_mode", "agent")
        # 是否启用 LLM 规划(默认 True; 失败时仍会 fallback 到规则式)
        self.use_llm_planner = self.config.get_effective_value(
            "agent.use_llm_planner", env_var="ANYTHING_AGENT_USE_LLM_PLANNER",
            default=True, value_type=bool,
        )
        self.max_planner_steps = self.config.get_effective_value(
            "agent.max_planner_steps", env_var="ANYTHING_AGENT_MAX_PLANNER_STEPS",
            default=3, value_type=int,
        )
        # 执行策略: "single_shot" (默认, 一次性规划 + 顺序执行) 或 "react" (多轮)
        self.execution_strategy = self.config.get_effective_value(
            "agent.execution_strategy", env_var="ANYTHING_AGENT_EXECUTION_STRATEGY",
            default="single_shot",
        )
        # Task TTTT-1 (#138): ReAct 模式最大轮数 — 默认 15 (旧 5 太短, 长 agentic 任务卡死).
        # 单次 invoke 可以 extra_params.max_iterations 临时覆盖.
        self.max_react_iterations = self.config.get_effective_value(
            "agent.max_react_iterations", env_var="ANYTHING_AGENT_MAX_REACT_ITER",
            default=15, value_type=int,
        )

        # 方向3 (自我验证闭环): 默认关。off 时 execute 行为零变化 (验证块整段跳过)。
        # enable_self_verify 总开关; verify_mode = off|auto|ask; max_correction 纠正预算。
        self.enable_self_verify = self.config.get_effective_value(
            "agent.enable_self_verify", env_var="ANYTHING_AGENT_SELF_VERIFY",
            default=False, value_type=bool,
        )
        self.verify_mode = str(self.config.get_config("agent.verify_mode", "off")).lower()
        self.max_correction = self.config.get_effective_value(
            "agent.max_correction", env_var="ANYTHING_AGENT_MAX_CORRECTION",
            default=2, value_type=int,
        )

        # UP-4 (方向1 阶段2, query refinement): 默认关。off 时 execute 行为零变化。
        # 开: agent.enable_query_refine=true。用户问得含糊时, 基于画像把问题补全/澄清
        # 后再规划; 严格保留原意, 失败一律 fail-open 用原问题。单次可 extra_params
        # .enable_query_refine 覆盖。query_refine_max_len: 超此长度的问题视为已足够具体,
        # 不折腾(省 LLM 调用, 也避免画蛇添足)。
        self.enable_query_refine = self.config.get_effective_value(
            "agent.enable_query_refine", env_var="ANYTHING_AGENT_QUERY_REFINE",
            default=False, value_type=bool,
        )
        self.query_refine_max_len = self.config.get_effective_value(
            "agent.query_refine_max_len", env_var="ANYTHING_AGENT_QUERY_REFINE_MAX_LEN",
            default=200, value_type=int,
        )
        # UP-4 ask 模式: auto=歧义时静默改写问题(原行为) / ask=歧义大时反问澄清(不改写,
        # 早退把一个澄清问题返回给用户)。默认 auto — 保持 enable_query_refine 开时的原行为不变;
        # 单次可 extra_params.query_refine_mode 覆盖。仍受 enable_query_refine 总开关 + 各 gate 约束。
        self.query_refine_mode = str(self.config.get_effective_value(
            "agent.query_refine_mode", env_var="ANYTHING_AGENT_QUERY_REFINE_MODE",
            default="auto", value_type=str,
        ) or "auto").strip().lower()
        if self.query_refine_mode not in ("auto", "ask"):
            self.query_refine_mode = "auto"

        # 方向4 (自主维护 / 第一级 建议性自主): 默认关。按需 self_reflect() 检视自身历史执行
        # → LLM 元级反思 → 改进提议 (dry-run); apply 仅在人显式审批后把"教训"落长期记忆。
        # 仅"提议", 绝不自动执行性维护 (务必分级, 全程 human-in-loop)。
        self.enable_self_reflection = self.config.get_effective_value(
            "agent.enable_self_reflection", env_var="ANYTHING_AGENT_SELF_REFLECTION",
            default=False, value_type=bool,
        )
        # 方向4 更高自主档 (建议性→预授权): standing-approval 名单 = 人预先授权可"自动执行"的
        # 维护 action_type (默认**空**=零自动执行=完全 human-in-loop)。run_maintenance_scan
        # 在 auto_apply 时, 仅对 (名单 ∩ 安全确定性算子) 自动 apply + 审计通知; 人设名单(预授权)
        # + 被通知 + 可随时清空(撤销)→ 更高自主档仍 human-in-loop。
        _env_aam = os.environ.get("ANYTHING_AGENT_AUTO_APPROVE_MAINTENANCE", "")
        if _env_aam:
            self.auto_approve_maintenance = set(t.strip() for t in _env_aam.split(",") if t.strip())
        else:
            _aam = self.config.get_config("agent.auto_approve_maintenance", [])
            self.auto_approve_maintenance = (
                set(str(t) for t in _aam if t) if isinstance(_aam, list) else set())

        # #1 技能自动沉淀 (借鉴 Hermes 学习闭环): 默认关 (与 human-in-loop 一致)。开后, 成功且
        # 复杂 (≥skill_distill_min_tools 个工具) 的任务收尾时, 后台 LLM 把"任务→工具序列→做法"
        # 提炼成可复用 skill 写入 skill 库, 下次同类任务自动注入 prompt。去重 + fail-open + 后台
        # 线程不阻塞主流程。
        self.enable_skill_distill = self.config.get_effective_value(
            "agent.enable_skill_distill", env_var="ANYTHING_AGENT_SKILL_DISTILL",
            default=False, value_type=bool,
        )
        self.skill_distill_min_tools = self.config.get_effective_value(
            "agent.skill_distill_min_tools", env_var="ANYTHING_AGENT_SKILL_DISTILL_MIN_TOOLS",
            default=2, value_type=int,
        )

        # Task W (#57): 危险工具白名单 — 这些工具被 LLM 选中时, 必须用户带
        # extra_params.approve_tools=[...] 显式通过才会执行, 否则返回 TOOL_APPROVAL_REQUIRED.
        default_dangerous = [
            "py_sandbox", "http_request", "file_write", "email_send", "shell_exec",
            "computer_use",   # 控制真实桌面 (截屏/鼠标/键盘) — 默认需审批
        ]
        env_approval = os.environ.get("ANYTHING_AGENT_APPROVAL", "")
        if env_approval:
            self.tool_approval_required = set(
                t.strip() for t in env_approval.split(",") if t.strip()
            )
        else:
            cfg = self.config.get_config("agent.tool_approval_required", default_dangerous)
            if isinstance(cfg, list):
                self.tool_approval_required = set(str(t) for t in cfg if t)
            else:
                self.tool_approval_required = set(default_dangerous)

        # Task FF (#66): 工具结果缓存 — 同 (tool_name, input) 命中缓存直接返回.
        default_cacheable = [
            "rag_search", "calculator", "currency_convert", "weather",
            "wikipedia", "datetime", "text_stats", "regex_extract",
            "json_query", "code_lint",
        ]
        env_cache_tools = os.environ.get("ANYTHING_AGENT_CACHEABLE_TOOLS", "")
        if env_cache_tools:
            self.cacheable_tools = set(
                t.strip() for t in env_cache_tools.split(",") if t.strip()
            )
        else:
            cfg = self.config.get_config("agent.cacheable_tools", default_cacheable)
            if isinstance(cfg, list):
                self.cacheable_tools = set(str(t) for t in cfg if t)
            else:
                self.cacheable_tools = set(default_cacheable)
        self.tool_cache_max_size = self.config.get_effective_value(
            "agent.tool_cache_max_size", env_var="ANYTHING_AGENT_TOOL_CACHE_SIZE",
            default=256, value_type=int,
        )
        self._tool_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._tool_cache_lock = threading.Lock()
        self._tool_cache_stats = {"hits": 0, "misses": 0}

        self.logger.info(
            f"Agent 模块初始化完成 (需审批工具: {sorted(self.tool_approval_required)}; "
            f"可缓存工具: {sorted(self.cacheable_tools)}; "
            f"cache 上限 {self.tool_cache_max_size})"
        )

    # ============================================================
    # 工具注册
    # ============================================================
    def register_tool(self, name: str, tool_func: Callable,
                      description: str, input_schema: Dict) -> bool:
        """注册工具 (实现 BaseAgent.register_tool 契约).

        description 与 input_schema 当前用于 LLM 驱动决策时作为工具元信息传给规划器.
        """
        if self.tool_registry is None:
            self.tool_registry = {}
        if hasattr(self.tool_registry, "register"):
            self.tool_registry.register(name, tool_func)
        else:
            self.tool_registry[name] = tool_func
        return True

    def unregister_tool(self, name: str) -> bool:
        """注销工具, 兼容 dict 和 ToolRegistry 两种风格."""
        if self.tool_registry is None:
            return False
        if hasattr(self.tool_registry, "unregister"):
            result = self.tool_registry.unregister(name)
            return bool(result) if result is not None else True
        if isinstance(self.tool_registry, dict):
            if name in self.tool_registry:
                del self.tool_registry[name]
                return True
            return False
        return False

    # ============================================================
    # 执行入口
    # ============================================================
    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """执行 Agent 任务, 统一接收标准 request dict.

        策略分发(由 self.execution_strategy 控制):
            - "single_shot" (默认): parse_task 一次性输出全部 steps -> 顺序执行 -> 聚合
            - "react": 多轮 observe-reflect-next 循环, 每步 LLM 决定下一动作

        ReAct 模式失败/不可用时优雅降级到 single_shot, 主链路永远有响应.
        """
        start_time = time.time()
        task = request.get("task")
        trace_id = request.get("trace_id")
        extra_params = request.get("extra_params") or {}

        # 方向4 定时提议+通知: maintenance_scan 请求 (可由 TaskScheduler 周期触发) → 早退,
        # 跑自维护检视聚合提议 + 通知, 不走正常任务执行。默认关 (须 enable_self_reflection)。
        if extra_params.get("maintenance_scan") and getattr(self, "enable_self_reflection", False):
            return {
                "code": "SUCCESS", "message": "maintenance_scan",
                "data": self.run_maintenance_scan(
                    tenant_id=self._memory_tenant(request), trace_id=trace_id,
                    scope=tuple(extra_params.get("maintenance_scope")
                                or ("behavior", "memory", "code_doc")),
                    auto_apply=extra_params.get("auto_apply"),
                ),
                "trace_id": trace_id, "retryable": False, "details": None,
                "cost_time": round(time.time() - start_time, 3),
            }

        # 契约: session_id 应由 RequestHandler._standardize_request 统一补齐
        #       trace_id  应由 ApiService middleware / ConsoleApp 应用层入口生成
        session_id = request.get("session_id")
        if not session_id:
            self.logger.error(
                f"[contract violation] SimpleAgent.execute 收到的 request 未含 session_id, "
                f"trace_id={trace_id}, task={task!r:.50s} -- "
                f"请检查 RequestHandler._standardize_request 是否在调用链上"
            )
            session_id = self._fallback_session_id()
        if not trace_id:
            self.logger.warning(
                f"[contract violation] SimpleAgent.execute 收到的 request 未含 trace_id, "
                f"session_id={session_id} -- 请检查应用层入口(ApiService/ConsoleApp)是否生成"
            )

        timeout = int(request.get("timeout") or self.timeout)
        max_retries = int(request.get("max_retries") or self.max_retries)
        execution_mode = extra_params.get("execution_mode", self.default_execution_mode)
        strategy = extra_params.get("execution_strategy", self.execution_strategy)

        # Task FFF (#92): 长期记忆 — 任务执行前注入相关 fact 作为前置上下文
        original_task = task
        memory_tenant = self._memory_tenant(request)
        memory_hits_used: List[Dict[str, Any]] = []

        # UP-4 (方向1 阶段2): query refinement — 用户问得含糊时, 先基于画像把问题
        # 补全/澄清, 再走下面的记忆/画像/历史注入与规划。只改 task(规划输入),
        # original_task 保持用户原话(history 展示 / 记忆抽取用)。默认关, 纠正递归跳过。
        refine_meta: Optional[Dict[str, Any]] = None
        _refine_on = extra_params.get("enable_query_refine")
        if _refine_on is None:
            _refine_on = self.enable_query_refine
        if (task and _refine_on and self.memory_enabled
                and self.long_term_memory is not None
                and not extra_params.get("_skip_history_prefix")):
            try:
                _refine_mode = str(extra_params.get("query_refine_mode")
                                   or self.query_refine_mode).strip().lower()
                _refined, refine_meta = self._refine_query(
                    task, memory_tenant, trace_id, mode=_refine_mode)
                if refine_meta and refine_meta.get("action") == "clarify":
                    # UP-4 ask 模式: 歧义大 → 反问澄清, 不执行任务, 早退把澄清问题作为回答返回。
                    # 用户的后续回答会作为新一轮请求进来 (本轮不落历史, 对齐 maintenance_scan 早退)。
                    self.logger.info(f"[refine] ask 模式反问澄清 (不执行任务): trace_id={trace_id}")
                    return {
                        "code": "SUCCESS", "message": "clarification_needed",
                        "data": {
                            "answer": refine_meta["question"],
                            "clarification_needed": True,
                            "session_id": session_id, "trace_id": trace_id,
                            "execution_mode": execution_mode,
                        },
                        "trace_id": trace_id, "retryable": False,
                        "details": {"query_refinement": refine_meta},
                        "cost_time": round(time.time() - start_time, 3),
                    }
                if refine_meta:
                    task = _refined
            except Exception as _rq_err:
                self.logger.warning(
                    f"[refine] query refinement 失败 (用原问题继续): "
                    f"trace_id={trace_id} err={_rq_err}"
                )

        if task and self.memory_enabled and self.long_term_memory is not None:
            try:
                augmented_task, memory_hits_used = self._inject_long_term_memory(
                    task=task, tenant_id=memory_tenant, trace_id=trace_id,
                )
                task = augmented_task
            except Exception as _mem_err:
                self.logger.warning(
                    f"[memory] inject 失败 (continue without memory): "
                    f"trace_id={trace_id} err={_mem_err}"
                )

        # UP-3 (方向1): always-on 注入用户画像 (不依赖 query, "这个人怎么做事")。
        # 纠正递归时跳过 (画像已在原 task, 避免叠加), 复用 _skip_history_prefix flag。
        if (task and self.memory_enabled and self.long_term_memory is not None
                and not extra_params.get("_skip_history_prefix")):
            try:
                task = self._inject_user_profile(task, memory_tenant)
            except Exception as _prof_err:
                self.logger.warning(f"[profile] inject 失败: trace_id={trace_id} err={_prof_err}")

        # ZZ-5: 多轮对话上下文 — 给 ReAct / single_shot 等非流式路径补历史 (默认流式已有).
        # 放在 long_term_memory 注入之后, 让 [对话历史] 块位于 (memory + 当前任务) 之上.
        # 方向3: 自我纠正递归时跳过 history 注入 (上下文已在原 task, 避免重复叠加)。
        if task and not extra_params.get("_skip_history_prefix"):
            _hist_prefix = self._history_prefix(session_id)
            if _hist_prefix:
                task = _hist_prefix + task

        # 方向3: 若本次是自我纠正递归, 把上轮验证失败的 feedback 拼进 task 引导修正
        _corr_fb = extra_params.get("_correction_feedback")
        if _corr_fb:
            task = f"{task}\n\n[上一轮验证未通过, 请针对性修正以下问题]\n{_corr_fb}"

        # ReAct 模式优先 (hybrid 由文档定义为固定流水线, 不走 ReAct)
        if strategy == "react" and execution_mode != "hybrid":
            react_result = self._react_execute(
                task=task, session_id=session_id, trace_id=trace_id,
                extra_params=extra_params, start_time=start_time,
            )
            if react_result is not None:
                # UP-4: react 也走了 refine(在分支前), 在此单一出口补记 details, 不碰 react 引擎
                if refine_meta and isinstance(react_result, dict):
                    _rd = react_result.get("details")
                    if not isinstance(_rd, dict):
                        _rd = {}
                    _rd["query_refinement"] = refine_meta
                    react_result["details"] = _rd
                return self._post_verify(request, react_result, original_task, start_time)
            self.logger.warning(
                f"[react] 多轮规划不可用(无 LLM 通道/任务不适合), fallback 到 single_shot: trace_id={trace_id}"
            )

        try:
            self.logger.info(
                f"Agent执行开始: session_id={session_id}, trace_id={trace_id}, "
                f"mode={execution_mode}, strategy={strategy}"
            )
            self._append_state_event(
                session_id=session_id, event_type="task_started", trace_id=trace_id,
                payload={
                    "task": task, "execution_mode": execution_mode,
                    "execution_strategy": strategy,
                    "timeout": timeout, "max_retries": max_retries,
                },
            )

            with trace_span(
                "agent.parse_task",
                attributes={
                    "anything.trace_id": trace_id or "-",
                    "agent.execution_mode": execution_mode,
                    "agent.execution_strategy": strategy,
                },
            ) as span:
                plan = self.parse_task(
                    task=task, session_id=session_id,
                    trace_id=trace_id, extra_params=extra_params,
                )
                span.set_attribute("agent.plan_source", str(plan.get("plan_source", "?")))
                span.set_attribute("agent.steps_count", len(plan.get("steps", [])))

            self._append_state_event(
                session_id=session_id, event_type="task_parsed", trace_id=trace_id,
                payload={"task": task, "step_count": len(plan.get("steps", []))},
            )

            tool_results = []
            _ss_timed_out = False
            for step in plan.get("steps", []):
                # AUDIT-2a: single_shot 步骤循环同样 enforce wall-clock 超时,
                # 超时则中止后续步骤, 返回已完成步骤的部分结果 (下方 response 标 AGENT_TIMEOUT)。
                if timeout and (time.time() - start_time) > timeout:
                    _ss_timed_out = True
                    self._append_state_event(
                        session_id=session_id, event_type="task_timeout", trace_id=trace_id,
                        payload={"elapsed": round(time.time() - start_time, 3), "timeout": timeout},
                    )
                    break
                step_id = step.get("step_id")
                tool_name = step.get("tool_name")
                self._append_state_event(
                    session_id=session_id, event_type="step_started", trace_id=trace_id,
                    payload={"step_id": step_id, "tool_name": tool_name},
                )
                result = self._call_tool_with_retry(
                    step=step, session_id=session_id,
                    trace_id=trace_id, max_retries=max_retries,
                )
                tool_results.append(result)
                if result.get("success"):
                    self._append_state_event(
                        session_id=session_id, event_type="step_finished", trace_id=trace_id,
                        payload={"step_id": step_id, "tool_name": tool_name},
                    )
                else:
                    self._append_state_event(
                        session_id=session_id, event_type="step_failed", trace_id=trace_id,
                        payload={"step_id": step_id, "tool_name": tool_name, "error": result.get("error")},
                    )

            aggregated = self.aggregate_results(
                task=task, session_id=session_id, trace_id=trace_id,
                tool_results=tool_results, execution_mode=execution_mode,
            )

            self._append_state_event(
                session_id=session_id, event_type="task_completed", trace_id=trace_id,
                payload={
                    "success_steps": len([r for r in tool_results if r.get("success")]),
                    "failed_steps": len([r for r in tool_results if not r.get("success")]),
                },
            )

            if self.state_store is not None:
                # Task QQQQ: 持久化 final answer 到 state, 让"切历史会话"能拿到完整回答
                # (之前只存 task + status, 历史展示只能给概要占位)
                self._save_state_safe(
                    session_id=session_id,
                    state={
                        "status": "completed",
                        "task": original_task,  # 存原 task, 不带长期记忆 prefix
                        "augmented_task": task if task != original_task else None,
                        "answer": aggregated.get("answer", "") if isinstance(aggregated, dict) else "",
                        "execution_mode": execution_mode,
                        "updated_at": time.time(),
                    },
                    trace_id=trace_id,
                )

            # Task III (#95): Reflection 反思环 — extra_params.enable_reflection=True
            #   或 execution_strategy="reflect" 时, 对初步答案做 critique → revise.
            reflection_meta: Optional[Dict[str, Any]] = None
            want_reflect = (
                strategy == "reflect"
                or bool(extra_params.get("enable_reflection", False))
            )
            if want_reflect and isinstance(aggregated, dict) and aggregated.get("answer"):
                try:
                    new_answer, reflection_meta = self._reflect_revise(
                        task=original_task,
                        initial_answer=str(aggregated.get("answer", "")),
                        trace_id=trace_id,
                    )
                    if new_answer is not None:
                        aggregated["answer"] = new_answer
                        aggregated["reflection_applied"] = True
                except Exception as _ref_err:
                    self.logger.warning(
                        f"[reflect] 反思失败 (保留原答案): trace_id={trace_id} err={_ref_err}"
                    )

            # Task FFF (#92): 任务完成后从对话抽取 fact 写入长期记忆 (best-effort)
            if self.memory_enabled and self.long_term_memory is not None:
                try:
                    self._extract_and_store_memory(
                        task=original_task,
                        final_answer=str(aggregated.get("answer") or "")
                            if isinstance(aggregated, dict) else "",
                        session_id=session_id,
                        tenant_id=memory_tenant,
                        trace_id=trace_id,
                    )
                except Exception as _mem_err:
                    self.logger.warning(
                        f"[memory] extract+store 失败 (不影响响应): "
                        f"trace_id={trace_id} err={_mem_err}"
                    )

            response = {
                "code": "AGENT_TIMEOUT" if _ss_timed_out else "SUCCESS",
                "message": (f"Agent 执行超过 {timeout}s 超时, 返回已完成步骤的部分结果"
                            if _ss_timed_out else "ok"),
                "data": aggregated, "trace_id": trace_id,
                "retryable": bool(_ss_timed_out), "details": None,
                "cost_time": round(time.time() - start_time, 3),
            }
            # 把命中的记忆条目 / 反思 meta 记到 details, 让前端 / debug 可见
            details: Dict[str, Any] = {}
            if memory_hits_used:
                details["memory_hits"] = memory_hits_used
            if refine_meta:
                details["query_refinement"] = refine_meta
            if reflection_meta:
                details["reflection"] = reflection_meta
            if details:
                response["details"] = details
            return self._post_verify(request, response, original_task, start_time)

        except Exception as e:
            self.logger.error(f"Agent执行异常: {str(e)}, trace_id={trace_id}, session_id={session_id}")
            self._append_state_event(
                session_id=session_id, event_type="task_failed", trace_id=trace_id,
                payload={"error": str(e)},
            )
            return self._handle_exception(
                exception=e, trace_id=trace_id, session_id=session_id, task=task,
            )

    # ============================================================
    # 方向3: 自我验证闭环 (验证 + 自纠正)
    # ============================================================
    def _build_verify_runner(self):
        """ExecutionVerifier 的执行器: 按 spec.type 跑确定性验证命令。
        pytest/lint/shell 走 subprocess, sql 走 sqlite。验证命令应来自可信调用方
        (extra_params.verify); 默认 enable_self_verify=off, 不主动执行任何东西。"""
        import subprocess
        import shlex
        import sys

        def runner(spec):
            t = spec.type
            target = (spec.target or "").strip()
            timeout_s = int((spec.args or {}).get("timeout", 60))
            try:
                if t == "pytest":
                    cmd = [sys.executable, "-m", "pytest", "-q"] + (shlex.split(target) if target else [])
                    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
                elif t == "lint":
                    cmd = [sys.executable, "-m", "pyflakes"] + (shlex.split(target) if target else ["."])
                    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
                elif t == "shell":
                    p = subprocess.run(target, shell=True, capture_output=True, text=True, timeout=timeout_s)
                elif t == "sql":
                    import sqlite3
                    con = sqlite3.connect((spec.args or {}).get("db") or ":memory:")
                    try:
                        con.executescript(target)
                        con.commit()
                        return {"exit_code": 0, "stdout": "ok", "stderr": ""}
                    except Exception as e:
                        return {"exit_code": 1, "stdout": "", "stderr": str(e)}
                    finally:
                        con.close()
                else:
                    return {"exit_code": 0, "stdout": "", "stderr": ""}
                return {"exit_code": p.returncode, "stdout": p.stdout or "", "stderr": p.stderr or ""}
            except subprocess.TimeoutExpired:
                return {"exit_code": 124, "stdout": "", "stderr": f"验证超时 (>{timeout_s}s)"}
            except FileNotFoundError as e:
                return {"exit_code": 127, "stdout": "", "stderr": f"验证命令不可用: {e}"}

        return runner

    def _collect_compliance_rules(self, extra) -> str:
        """合规检查的规范来源: 优先 extra_params.compliance_rules; 否则尽力取项目级
        记忆 (AGENTS.md / ProjectMemory)。取不到返回空串 → compliance 自动放行。"""
        explicit = (extra or {}).get("compliance_rules")
        if explicit:
            return str(explicit)
        pm = getattr(self, "project_memory", None)
        if pm is None:
            pm = getattr(getattr(self, "deps", None), "project_memory", None)
        if pm is not None:
            for meth in ("get_memory_text", "as_text", "get_content", "render", "load"):
                fn = getattr(pm, meth, None)
                if callable(fn):
                    try:
                        txt = fn()
                        if txt:
                            return str(txt)
                    except Exception:
                        pass
        return ""

    def _post_verify(self, request, response, original_task, start_time):
        """对执行结果跑验证; auto 模式失败时带 feedback 递归纠正 (预算护栏)。
        enable_self_verify=off / verify_mode=off 时原样返回 (零影响)。"""
        if not getattr(self, "enable_self_verify", False) or self.verify_mode == "off":
            return response
        if not isinstance(response, dict):
            return response

        extra = dict(request.get("extra_params") or {})
        attempt = int(extra.get("_verify_attempt", 0))
        specs = collect_specs(extra)
        if not specs:
            return response

        trace_id = request.get("trace_id")
        session_id = request.get("session_id") or self._fallback_session_id()
        llm_call = self._resolve_llm_planner(trace_id=trace_id)
        if llm_call is None:
            # 无 LLM 通道: 终态确认无法判, 退化为放行 (只让 execution 验证生效)
            llm_call = lambda _p: '{"completed": true}'
        registry = make_registry(
            runner=self._build_verify_runner(), llm_call=llm_call,
            rules_provider=lambda: self._collect_compliance_rules(extra),
        )

        vresults = run_verifiers(
            goal=original_task, result=response.get("data") or response,
            specs=specs, registry=registry,
        )
        if response.get("details") is None:
            response["details"] = {}
        response["details"]["verification"] = [
            {"verifier": r.verifier, "passed": r.passed, "feedback": (r.feedback or "")[:500]}
            for r in vresults
        ]
        failed = [r for r in vresults if not r.passed]
        response["details"]["verify_passed"] = (len(failed) == 0)
        if not failed:
            return response

        fixable_fb = [r.feedback for r in failed if r.fixable and r.feedback]
        self._append_state_event(
            session_id=session_id, event_type="verify_failed", trace_id=trace_id,
            payload={"attempt": attempt, "failed": [r.verifier for r in failed]},
        )

        # ask 模式: 不自动纠正, 标记需用户确认 + 缺口
        if self.verify_mode == "ask":
            response["details"]["needs_user_confirm"] = True
            response["details"]["verify_gaps"] = fixable_fb
            return response

        # auto 模式: 预算内 + 可修 + 未超时 → 带 feedback 递归纠正
        within_budget = attempt < self.max_correction
        within_time = (time.time() - start_time) < self.timeout
        if fixable_fb and within_budget and within_time:
            self._append_state_event(
                session_id=session_id, event_type="self_correct", trace_id=trace_id,
                payload={"attempt": attempt + 1},
            )
            new_extra = dict(extra)
            new_extra["_verify_attempt"] = attempt + 1
            new_extra["_correction_feedback"] = "\n".join(fixable_fb)[:3000]
            new_extra["_skip_history_prefix"] = True
            new_request = dict(request)
            new_request["extra_params"] = new_extra
            return self.execute(new_request)

        # 预算耗尽 / 不可修 → 返回 + 标记缺口
        response["details"]["verify_gaps"] = fixable_fb
        return response

    # ============================================================
    # #1 技能自动沉淀 (借鉴 Hermes 学习闭环): 成功复杂任务 → 提炼可复用 skill
    # ============================================================
    def _distill_skill_async(self, task, tool_results, final_answer, trace_id=None):
        """成功复杂任务收尾时, 后台线程沉淀 skill (不阻塞主流程 / 不延迟 done)。默认关。"""
        try:
            if not getattr(self, "enable_skill_distill", False):
                return
            n_tools = len([tr for tr in (tool_results or []) if tr and tr.get("tool_name")])
            if n_tools < int(getattr(self, "skill_distill_min_tools", 2)):
                return  # 简单任务不沉淀
            if not (final_answer and str(final_answer).strip()):
                return
            import threading
            threading.Thread(
                target=self._distill_skill,
                args=(task, tool_results, final_answer, trace_id),
                daemon=True,
            ).start()
        except Exception:
            pass  # fail-open: 沉淀永不影响主流程

    def _distill_skill(self, task, tool_results, final_answer, trace_id=None):
        """LLM 从一次成功任务提炼一条可复用 skill 并写入 skill 库。去重 + 全程 fail-open。"""
        try:
            llm_call = self._resolve_llm_planner(trace_id=trace_id)
            if llm_call is None:
                return None
            tools_used = [tr.get("tool_name") for tr in (tool_results or [])
                          if tr and tr.get("tool_name")]
            prompt = (
                "下面是一次已成功完成的任务。请把它提炼成一条【可复用技能(skill)】, 供以后遇到\n"
                "同类任务时直接复用 (作为提示注入)。要通用、可迁移, 不要带这次的具体数据。\n"
                f"【任务】{str(task)[:500]}\n"
                f"【用到的工具】{tools_used}\n"
                f"【最终结果(节选)】{str(final_answer)[:500]}\n\n"
                "只输出 JSON, 不要任何其他文字:\n"
                '{"name": "蛇形小写英文名", "description": "一句话描述", '
                '"triggers": ["会触发这类任务的关键词(3-6个,中英文皆可)"], '
                '"tools": ["用到的工具名"], '
                '"body": "遇到这类任务用什么步骤/工具/注意点去做的简明指南(中文,100-300字)"}'
            )
            raw = llm_call(prompt) or ""
            import json as _json
            import re as _re
            m = _re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return None
            data = _json.loads(m.group(0))
            name = str(data.get("name") or "").strip()
            triggers = [str(t).strip() for t in (data.get("triggers") or []) if str(t).strip()]
            body = str(data.get("body") or "").strip()
            if not (name and triggers and body):
                return None
            from skills_module.impl import Skill, get_skill_registry
            reg = get_skill_registry()
            if reg.find_by_triggers(triggers) is not None:
                self.logger.info(f"[skill-distill] 已有同类 skill, 跳过: name={name}")
                return None  # 去重: 已有高度重叠的 skill (v1 不合并)
            skill = Skill(
                name=name,
                description=str(data.get("description") or task)[:120],
                triggers=triggers,
                tools=[str(t) for t in (data.get("tools") or tools_used) if t],
                priority=1,
                body=body,
            )
            path = reg.save_skill(skill, source="auto")
            if path:
                self.logger.info(
                    f"[skill-distill] 沉淀新技能: name={name} triggers={triggers} → {path}")
            return path
        except Exception as e:
            self.logger.warning(f"[skill-distill] 沉淀失败 (忽略): {e}")
            return None

    # ============================================================
    # 任务规划 (single_shot 路径)
    # ============================================================
    def parse_task(
            self,
            task: str,
            session_id: str,
            trace_id: Optional[str] = None,
            extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """解析任务并生成执行计划.

        策略:
            1. 若 use_llm_planner=True 且能拿到 LLM 调用通道 -> 让 LLM 规划
            2. LLM 规划失败 -> fallback 规则式
            3. hybrid 模式或没有 LLM 通道 -> 直接走规则式
        """
        extra_params = extra_params or {}
        execution_mode = extra_params.get("execution_mode", self.default_execution_mode)

        if self.use_llm_planner and execution_mode != "hybrid":
            llm_steps = self._llm_plan_task(
                task=task, trace_id=trace_id, extra_params=extra_params,
            )
            if llm_steps:
                return {
                    "session_id": session_id, "task": task,
                    "steps": llm_steps, "plan_source": "llm",
                }

        steps = self._rule_based_plan_task(
            task=task, execution_mode=execution_mode,
            trace_id=trace_id, extra_params=extra_params,
        )
        return {
            "session_id": session_id, "task": task,
            "steps": steps, "plan_source": "rule_based",
        }

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
            input_data = step.get("input_data") or {}
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

    # ============================================================
    # 结果聚合
    # ============================================================
    def aggregate_results(
            self,
            task: str,
            session_id: str,
            trace_id: Optional[str],
            tool_results: List[Dict[str, Any]],
            execution_mode: str,
    ) -> Dict[str, Any]:
        """聚合工具结果, 输出统一结构."""
        steps = []
        summaries = []
        rag_answer = ""
        llm_answer = ""
        final_citations = []
        final_retrieved_chunks = []

        for item in tool_results:
            tool_name = item.get("tool_name")
            success = item.get("success", False)
            output = item.get("output")
            steps.append({
                "step_id": item.get("step_id"),
                "tool_name": tool_name, "success": success,
            })

            if not success:
                summaries.append({
                    "tool_name": tool_name,
                    "summary": f"失败: {item.get('error', 'unknown error')}",
                })
                continue

            data = output.get("data") if isinstance(output, dict) else None

            if tool_name == "rag_search":
                if isinstance(data, dict):
                    rag_answer = data.get("answer", "") or ""
                    final_citations = data.get("citations", []) or []
                    final_retrieved_chunks = data.get("retrieved_chunks", []) or []
                summaries.append({
                    "tool_name": tool_name,
                    "summary": self._summarize_tool_output(output),
                })
                continue

            if tool_name == "llm_generate":
                if isinstance(data, dict):
                    llm_answer = (
                        data.get("answer") or data.get("text")
                        or data.get("content") or ""
                    )
                else:
                    llm_answer = str(output)
                summaries.append({
                    "tool_name": tool_name,
                    "summary": self._summarize_tool_output(output),
                })
                continue

            summaries.append({
                "tool_name": tool_name,
                "summary": self._summarize_tool_output(output),
                "images": self._extract_image_urls(output),  # XXXX-11
            })

        if execution_mode == "hybrid":
            final_answer = llm_answer or rag_answer or "任务执行完成, 但未生成可展示内容."
        else:
            final_answer = llm_answer or rag_answer
            if not final_answer:
                success_summaries = [x["summary"] for x in summaries if not x["summary"].startswith("失败")]
                final_answer = success_summaries[0] if success_summaries else "任务执行完成, 但未生成可展示内容."

        # ZZ-6 (#122): final_answer 若是裸工具名 (LLM 幻觉) → 兜底, 别把工具名吐给用户
        if final_answer and not self._sanitize_final_answer(final_answer):
            final_answer = "任务执行完成, 但未生成可展示内容."

        # Task SSSS: 工具直接输出 raw JSON 时 (例如 datetime / calculator 都返 dict),
        # 用一次 LLM 合成自然语言. 检测条件: final_answer 看起来是 JSON / dict 字面值.
        if final_answer and self._looks_like_raw_json(final_answer):
            synthesized = self._synthesize_natural_answer(
                task=task, raw=final_answer, trace_id=trace_id,
            )
            if synthesized:
                final_answer = synthesized

        return {
            "answer": final_answer,
            "session_id": session_id, "trace_id": trace_id,
            "execution_mode": execution_mode,
            "steps": steps, "tool_results_summary": summaries,
            "citations": final_citations,
            "retrieved_chunks": final_retrieved_chunks,
        }

    @staticmethod
    def _looks_like_raw_json(s: str) -> bool:
        """检测字符串是不是 raw JSON/dict 字面值 (开头 { 或 [ 且能 json.loads)."""
        if not isinstance(s, str):
            return False
        s = s.strip()
        if not (s.startswith("{") or s.startswith("[")):
            return False
        try:
            import json as _j
            _j.loads(s)
            return True
        except Exception:
            return False

    def _synthesize_natural_answer(
        self, task: str, raw: str, trace_id: Optional[str],
    ) -> str:
        """让 LLM 把 raw 工具输出转成自然语言回答用户问题. 失败返空字符串."""
        try:
            planner = self._resolve_llm_planner(trace_id)
            if planner is None:
                return ""
            prompt = (
                "用户问题: " + str(task) + "\n\n"
                "工具的原始输出 (JSON):\n" + str(raw)[:1500] + "\n\n"
                "请基于上面的工具输出, 用中文自然语言简洁回答用户问题. "
                "不要再贴 JSON 也不要解释字段名, 直接给答案."
            )
            ans = planner(prompt)
            if isinstance(ans, str) and ans.strip():
                return ans.strip()
        except Exception as e:
            self.logger.warning(f"[synthesize] LLM 合成自然语言失败, 回退 raw: {e}")
        return ""

    # ============================================================
    # LLM / 工具 registry 辅助
    # ============================================================
    def _resolve_llm_planner(self, trace_id: Optional[str]) -> Optional[Callable[[str], str]]:
        """优先用显式注入的 llm_planner; 否则通过 tool_registry 的 llm_generate 调 LLM."""
        if self.llm_planner is not None:
            return self.llm_planner
        if self.tool_registry is None:
            return None

        tool = None
        if hasattr(self.tool_registry, "get"):
            tool = self.tool_registry.get("llm_generate")
        elif isinstance(self.tool_registry, dict):
            tool = self.tool_registry.get("llm_generate")
        if tool is None:
            return None

        def _wrap(prompt: str) -> str:
            result = tool({"prompt": prompt, "trace_id": trace_id})
            if isinstance(result, dict):
                data = result.get("data") or {}
                return str(data.get("text") or data.get("answer") or "") or ""
            return str(result) if result is not None else ""

        return _wrap

    def _available_tool_names(self) -> List[str]:
        if self.tool_registry is None:
            return []
        if hasattr(self.tool_registry, "list_tools"):
            try:
                return list(self.tool_registry.list_tools())
            except Exception:
                pass
        if isinstance(self.tool_registry, dict):
            return list(self.tool_registry.keys())
        return []

    def _tool_descriptions(self) -> Dict[str, str]:
        """从 tool_registry 抽出每个工具的描述. registry 没暴露 describe* 时返回空 dict."""
        if self.tool_registry is None:
            return {}
        if hasattr(self.tool_registry, "describe_all"):
            try:
                out = self.tool_registry.describe_all()
                if isinstance(out, dict):
                    return {str(k): str(v) for k, v in out.items()}
            except Exception:
                pass
        if hasattr(self.tool_registry, "describe"):
            names = self._available_tool_names()
            try:
                return {n: str(self.tool_registry.describe(n) or "") for n in names}
            except Exception:
                pass
        return {}

    # ============================================================
    # 状态事件 / 异常 / fallback
    # ============================================================
    def _append_state_event(
            self,
            session_id: str,
            event_type: str,
            trace_id: Optional[str],
            payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """安全写入状态事件: 失败不阻断主任务."""
        if self.state_store is None:
            return
        event = {
            "session_id": session_id, "event_type": event_type,
            "trace_id": trace_id, "payload": payload or {},
            "created_at": time.time(),
        }
        try:
            if hasattr(self.state_store, "append_event"):
                self.state_store.append_event(session_id, event)
        except Exception as e:
            self.logger.warning(
                f"状态事件写入失败(已忽略): session_id={session_id}, "
                f"event_type={event_type}, error={str(e)}"
            )

    def _load_history(self, session_id, max_turns: int = 6):
        """从 state_store 读最近 N 轮对话, 返回 [{role, content}, ...].

        修 Agent "金鱼记忆": 之前 Agent 完全不读会话历史 (只有 long_term_memory
        facts), 多轮对话不连贯. 现在跟 RAG 一样从 state.events 读最近 N 轮注入 prompt.
        读的是历史 (不含当前轮, 当前轮流式完成后才持久化).
        """
        if not session_id or not self.state_store:
            return []
        try:
            state = self.state_store.get_state(session_id)
        except Exception:
            return []
        if not isinstance(state, dict):
            return []
        events = state.get("events") or []
        max_msgs = max(1, max_turns) * 2
        if len(events) > max_msgs:
            events = events[-max_msgs:]
        msgs = []
        for ev in events:
            if isinstance(ev, dict):
                role = ev.get("role")
                content = ev.get("content")
                if role in ("user", "assistant") and isinstance(content, str) and content:
                    msgs.append({"role": role, "content": content})
        return msgs

    def _history_prefix(self, session_id, max_turns: int = 6) -> str:
        """ZZ-5: 把最近 N 轮对话拼成可注入 prompt 顶部的 [对话历史] 块. 无/失败返回 ''.

        ZZ-1 只给默认流式 (_run_stream_direct) 注入了历史; ReAct / plan / 非流式 execute
        / single_shot 仍是金鱼记忆. 这个 helper 给那几条路径统一补多轮上下文.
        """
        try:
            history = self._load_history(session_id, max_turns=max_turns)
        except Exception:
            return ""
        lines = []
        for h in history:
            who = "用户" if h.get("role") == "user" else "助手"
            c = (h.get("content") or "").strip()
            if c:
                lines.append(f"{who}: {c}")
        if not lines:
            return ""
        return "[对话历史]\n" + "\n".join(lines) + "\n\n---\n\n"

    def _save_state_safe(
            self,
            session_id: str,
            state: Dict[str, Any],
            trace_id: Optional[str],
    ) -> None:
        """安全保存聚合状态 + append 本轮 user/assistant 到 events 历史链.

        之前 bug: save_state 是 full-replace, 每轮覆盖前轮, 切回历史只看到最后一条.
        现在 merge:
          1. 读老 state 拿到 events list
          2. 把本轮 user_task + assistant_answer 各 append 一个 role event
          3. 顶层 task/answer 仍存 (向后兼容; 也方便 list_sessions 抽 title)
          4. save_state 写回, events 保留了完整对话历史
        失败不阻断主任务.
        """
        if self.state_store is None:
            return
        try:
            new_state = dict(state) if isinstance(state, dict) else {}
            # 1. 读老 events, 兼容首次 (无文件) 情况
            old_events: list = []
            if hasattr(self.state_store, "get_state"):
                try:
                    old = self.state_store.get_state(session_id) or {}
                    if isinstance(old, dict):
                        old_events = list(old.get("events") or [])
                except Exception:
                    old_events = []

            # 2. 本轮 task / answer → 追加 2 个 role event
            #    task 用 state.task (已经是 original_task, 不含长期记忆 prefix)
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()))
            user_task = new_state.get("task")
            asst_answer = new_state.get("answer")
            if user_task:
                old_events.append({
                    "role": "user",
                    "content": str(user_task),
                    "timestamp": now_iso,
                    "trace_id": trace_id,
                    "type": new_state.get("execution_mode") or "agent",
                })
            if asst_answer:
                old_events.append({
                    "role": "assistant",
                    "content": str(asst_answer),
                    "timestamp": now_iso,
                    "trace_id": trace_id,
                    "type": new_state.get("execution_mode") or "agent",
                })
            new_state["events"] = old_events

            if hasattr(self.state_store, "save_state"):
                self.state_store.save_state(session_id, new_state)
        except Exception as e:
            self.logger.warning(
                f"状态保存失败(已忽略): session_id={session_id}, "
                f"trace_id={trace_id}, error={str(e)}"
            )

    def _fallback_session_id(self) -> str:
        """仅兼容兜底使用, 不作为主路径."""
        return f"{self.session_prefix}_{uuid.uuid4().hex[:12]}"

    # ============================================================
    # 长期记忆/画像注入已抽到 components/memory_injection.py (MemoryMixin, 优化③);
    # 方向4 自维护抽到 self_maintenance.py。_resolve_project_root 留在 impl —
    # 靠 __file__ 上溯定位项目根, 位置相关 (移到 components/ 会算错层级)。
    # ============================================================
    def _resolve_project_root(self) -> Optional[str]:
        """定位项目根 (impl.py 在 business/agent_module/core/ 下, 上溯 4 级)。"""
        try:
            here = os.path.abspath(__file__)
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))
            return root if os.path.isdir(root) else None
        except Exception:
            return None

    # ============================================================
    # Task III (#95): Reflection 反思环 (Reflexion / Self-critique)
    # ============================================================

    def _reflect_revise(
        self, task: str, initial_answer: str, trace_id: Optional[str],
    ) -> tuple:
        """对初步答案做 critique → revise. 类 Reflexion 论文 / OpenAI o1 思路.

        2 步 LLM 调用:
          1. critique: "Given the task and initial answer, identify any flaws,
             missing info, or improvements."
          2. revise: "Given the critique, produce an improved answer."

        失败 (LLM 不可用 / critique 解析失败) → 返 (None, meta), 主路径保留原答案.
        meta: {critique_text, n_issues, llm_calls, cost_ms}.
        """
        llm = self._resolve_llm_planner(trace_id=trace_id)
        if llm is None:
            return None, {"skipped": "no_llm"}

        meta: Dict[str, Any] = {"llm_calls": 0}
        t0 = time.time()

        # ----- 1. Critique -----
        critique_prompt = (
            "你是严格的答案评审. 给定任务和初步答案, 找出缺陷 / 缺失 / 模糊点 / 可改进处.\n\n"
            f"[任务]\n{task}\n\n"
            f"[初步答案]\n{initial_answer}\n\n"
            "请返回 JSON: {\n"
            '  "issues": ["缺陷1", "缺陷2", ...],\n'
            '  "missing_info": ["缺哪些上下文", ...],\n'
            '  "overall_quality": 1-5,\n'
            '  "should_revise": true|false\n'
            "}\n"
            "只返回 JSON, 别加任何解释."
        )
        try:
            critique_raw = llm(critique_prompt)
            meta["llm_calls"] += 1
        except Exception as e:
            return None, {"skipped": "critique_llm_failed", "err": str(e)}

        critique = self._parse_reflection_json(critique_raw or "")
        if not critique:
            return None, {"skipped": "critique_json_parse_failed", "raw": (critique_raw or "")[:200]}

        meta["critique"] = critique
        meta["n_issues"] = len(critique.get("issues") or [])
        meta["overall_quality"] = critique.get("overall_quality")

        # 自评分高 + LLM 自己说不用 revise → 跳过, 节省一轮调用
        if not critique.get("should_revise", True) and int(critique.get("overall_quality") or 0) >= 4:
            meta["skipped_revise"] = "self_eval_good"
            meta["cost_ms"] = int((time.time() - t0) * 1000)
            return None, meta

        # ----- 2. Revise -----
        issues_str = "\n".join(f"- {x}" for x in (critique.get("issues") or [])[:5])
        missing_str = "\n".join(f"- {x}" for x in (critique.get("missing_info") or [])[:5])
        revise_prompt = (
            "你需要根据评审意见把答案修订得更好.\n\n"
            f"[任务]\n{task}\n\n"
            f"[初步答案]\n{initial_answer}\n\n"
            f"[发现的问题]\n{issues_str or '(无)'}\n\n"
            f"[缺失信息]\n{missing_str or '(无)'}\n\n"
            "请直接返回修订后的最终答案 (不要解释为什么修订, 不加 '修订后:' 等前缀)."
        )
        try:
            revised = llm(revise_prompt)
            meta["llm_calls"] += 1
        except Exception as e:
            return None, {**meta, "skipped": "revise_llm_failed", "err": str(e)}

        if not revised or not str(revised).strip():
            return None, {**meta, "skipped": "revise_empty"}

        meta["cost_ms"] = int((time.time() - t0) * 1000)
        return str(revised).strip(), meta

    @staticmethod
    def _parse_reflection_json(raw: str) -> Optional[Dict[str, Any]]:
        """LLM 返 JSON dict, 同 long_term_memory 的 _parse_extracted_json 兼容
        markdown 围栏 / 前后解释文字."""
        import json as _json
        import re as _re

        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = _re.sub(r"^```[a-zA-Z]*\n", "", raw)
            raw = _re.sub(r"\n```\s*$", "", raw)
            raw = raw.strip()
        try:
            obj = _json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        m = _re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            obj = _json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    @staticmethod
    def _extract_image_urls(output: Any) -> list:
        """Task XXXX-11 (#158): 从工具输出 dict 抽 image url 列表 (image_generate 等).

        前端 _collectGeneratedImages 优先读 tool_results_summary[i].images (结构),
        不再 fallback 正则扫 description.
        """
        if not isinstance(output, dict):
            return []
        data = output.get("data")
        urls = []
        if isinstance(data, dict):
            imgs = data.get("images")
            if isinstance(imgs, list):
                urls.extend([u for u in imgs if isinstance(u, str) and u.startswith("http")])
            single = data.get("image_url")
            if isinstance(single, str) and single.startswith("http") and single not in urls:
                urls.append(single)
        return urls

    def _summarize_tool_output(self, output: Any) -> str:
        """将工具输出压缩为简短摘要 (给 LLM 看 + 给前端展示).

        长度上限 2000 — 给前端足够空间展示完整 description / answer,
        同时不让 LLM 上下文爆掉 (2000 char ≈ 600 token).
        优先级: data.description > data.answer > data.text > data.content > 顶层
        """
        _LIMIT = 2000
        if isinstance(output, dict):
            if "data" in output and isinstance(output["data"], dict):
                data = output["data"]
                if "description" in data:
                    return str(data["description"])[:_LIMIT]
                if "answer" in data:
                    return str(data["answer"])[:_LIMIT]
                if "text" in data:
                    return str(data["text"])[:_LIMIT]
                if "content" in data:
                    return str(data["content"])[:_LIMIT]
                if "summary" in data:
                    return str(data["summary"])[:_LIMIT]
                try:
                    import json as _json
                    return _json.dumps(data, ensure_ascii=False)[:_LIMIT]
                except Exception:
                    return str(data)[:_LIMIT]
            if "answer" in output:
                return str(output["answer"])[:_LIMIT]
            if "text" in output:
                return str(output["text"])[:_LIMIT]
            if "content" in output:
                return str(output["content"])[:_LIMIT]
            return str(output)[:_LIMIT]
        return str(output)[:_LIMIT]

    def _sanitize_final_answer(self, answer: Any) -> str:
        """ZZ-6 (#122): 防 Agent 偶发把裸工具名 (如 'llm_generate') 当最终答案返回.

        根因: LLM 偶尔幻觉直接输出 {"final_answer": "llm_generate"} (把工具名当答案);
        _parse_react_response 会原样透传. 这里检测 final answer 是否恰好是某已注册工具名,
        是则返回 '' (无效), 调用方走兜底文案 — 用户不再看到裸工具名.
        """
        if not isinstance(answer, str):
            return answer
        stripped = answer.strip().strip('`"\' .。\n\t')
        if not stripped:
            return answer
        try:
            tools = set(self._available_tool_names() or [])
        except Exception:
            tools = set()
        tools |= {"llm_generate", "rag_search"}  # registry 为空时也兜底
        if stripped in tools:
            self.logger.warning(
                f"[#122] final_answer 命中裸工具名 {stripped!r}, 判为无效输出, 走兜底"
            )
            return ""
        return answer

    def _handle_exception(
            self,
            exception: Exception,
            trace_id: Optional[str],
            session_id: Optional[str],
            task: Optional[str],
    ) -> Dict[str, Any]:
        """统一异常处理 (委托给 deps_module.handle_exception_to_envelope)."""
        return handle_exception_to_envelope(
            exception_handler=self.exception_handler,
            exception=exception,
            trace_id=trace_id,
            fallback_code="AGENT_RUN_FAILED",
            fallback_message="Agent执行失败",
            stage="agent",
            context={"session_id": session_id, "task": task},
        )

    # ============================================================
    # 旧抽象接口兼容
    # ============================================================
    def call_agent(
            self,
            task: str,
            session_id: Optional[str] = None,
            trace_id: Optional[str] = None,
            extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """兼容旧抽象接口: 将旧风格调用转成统一 request dict, 再复用 execute()."""
        request = {
            "type": "agent", "task": task,
            "trace_id": trace_id, "extra_params": extra_params or {},
        }
        if session_id:
            request["session_id"] = session_id
        return self.execute(request)
