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
)

from deps_module import BasicDeps, build_basic_deps, handle_exception_to_envelope
from observability_module import trace_span


class SimpleAgent(
    BaseAgent,
    ReActEngineMixin,
    ToolExecutorMixin,
    StreamingMixin,
    PromptBuilderMixin,
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

        # LLM 规划器: 可显式注入 callable (prompt -> str);
        # 未注入时回退到 tool_registry["llm_generate"]
        self.llm_planner = llm_planner

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
        # ReAct 模式最大轮数
        self.max_react_iterations = self.config.get_effective_value(
            "agent.max_react_iterations", env_var="ANYTHING_AGENT_MAX_REACT_ITER",
            default=5, value_type=int,
        )

        # Task W (#57): 危险工具白名单 — 这些工具被 LLM 选中时, 必须用户带
        # extra_params.approve_tools=[...] 显式通过才会执行, 否则返回 TOOL_APPROVAL_REQUIRED.
        default_dangerous = [
            "py_sandbox", "http_request", "file_write", "email_send", "shell_exec",
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

        # ReAct 模式优先 (hybrid 由文档定义为固定流水线, 不走 ReAct)
        if strategy == "react" and execution_mode != "hybrid":
            react_result = self._react_execute(
                task=task, session_id=session_id, trace_id=trace_id,
                extra_params=extra_params, start_time=start_time,
            )
            if react_result is not None:
                return react_result
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
            for step in plan.get("steps", []):
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
                self._save_state_safe(
                    session_id=session_id,
                    state={
                        "status": "completed", "task": task,
                        "execution_mode": execution_mode, "updated_at": time.time(),
                    },
                    trace_id=trace_id,
                )

            return {
                "code": "SUCCESS", "message": "ok",
                "data": aggregated, "trace_id": trace_id,
                "retryable": False, "details": None,
                "cost_time": round(time.time() - start_time, 3),
            }

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
            })

        if execution_mode == "hybrid":
            final_answer = llm_answer or rag_answer or "任务执行完成, 但未生成可展示内容."
        else:
            final_answer = llm_answer or rag_answer
            if not final_answer:
                success_summaries = [x["summary"] for x in summaries if not x["summary"].startswith("失败")]
                final_answer = success_summaries[0] if success_summaries else "任务执行完成, 但未生成可展示内容."

        return {
            "answer": final_answer,
            "session_id": session_id, "trace_id": trace_id,
            "execution_mode": execution_mode,
            "steps": steps, "tool_results_summary": summaries,
            "citations": final_citations,
            "retrieved_chunks": final_retrieved_chunks,
        }

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

    def _save_state_safe(
            self,
            session_id: str,
            state: Dict[str, Any],
            trace_id: Optional[str],
    ) -> None:
        """安全保存聚合状态: 失败不阻断主任务."""
        if self.state_store is None:
            return
        try:
            if hasattr(self.state_store, "save_state"):
                self.state_store.save_state(session_id, state)
        except Exception as e:
            self.logger.warning(
                f"状态保存失败(已忽略): session_id={session_id}, "
                f"trace_id={trace_id}, error={str(e)}"
            )

    def _fallback_session_id(self) -> str:
        """仅兼容兜底使用, 不作为主路径."""
        return f"{self.session_prefix}_{uuid.uuid4().hex[:12]}"

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
