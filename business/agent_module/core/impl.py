# -*- coding: utf-8 -*-
"""
Agent 模块具体实现类
负责任务解析、工具调用、状态记录与结果聚合
"""

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from typing import Dict, Any, List, Optional, Callable

from .base import BaseAgent

from deps_module import BasicDeps, build_basic_deps, handle_exception_to_envelope
from observability_module import trace_span
from common_utils_module import (
    get_project_memory, get_hook_registry, BlockedError,
    get_skill_registry, inject_skills_into_prompt,
)


class SimpleAgent(BaseAgent):
    """标准 Agent 实现：任务解析 -> 工具调用 -> 状态记录 -> 结果聚合

    任务解析(parse_task)优先使用 LLM 规划:
        - 通过 LLM 让模型自己决定调用工具序列,符合"智能代理"语义
        - 任一失败(LLM 不可用 / JSON 解析失败 / 工具不在注册表)→ fallback 到规则式
        - 通过配置 agent.use_llm_planner=False 可强制关闭 LLM 规划
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
        # 基础依赖优先走 DI 注入；未注入时构造一套（向后兼容）
        deps = deps or build_basic_deps()
        self.utils = deps.utils
        self.logger = deps.logger
        self.config = deps.config
        self.exception_handler = deps.exception_handler

        # LLM 规划器: 可显式注入 callable (prompt -> str);
        # 未注入时回退到 tool_registry["llm_generate"]
        self.llm_planner = llm_planner

        self.state_store = state_store
        self.tool_registry = tool_registry
        # 关键配置项走 get_effective_value, 允许环境变量覆盖
        # (运维不改代码即可调参; 详见 docs/configuration-priority.md)
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
        # LLM 规划最多生成的 step 数,避免无限链路
        self.max_planner_steps = self.config.get_effective_value(
            "agent.max_planner_steps", env_var="ANYTHING_AGENT_MAX_PLANNER_STEPS",
            default=3, value_type=int,
        )
        # 执行策略: "single_shot" (默认,一次性规划 + 顺序执行) 或 "react" (多轮 observe-reflect-next)
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
        # 借鉴 Codex approval modes 模式. 默认拉一份合理基线;
        # 运维通过 config agent.tool_approval_required 或 env ANYTHING_AGENT_APPROVAL 覆盖.
        default_dangerous = [
            "py_sandbox",      # 跑任意 Python, 沙箱后仍是高敏感
            "http_request",    # 外网调用, 可能泄露 / 计费
            "file_write",      # 写文件
            "email_send",      # 发邮件 (业务影响)
            "shell_exec",      # 假设未来有 shell 工具
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

        # Task FF (#66): 工具结果缓存 — 同 (tool_name, input) 命中缓存直接返回,
        # 不再真调工具. 减少 LLM 重复触发同名工具时的开销 (rag_search 同 query 等).
        # 仅对"幂等 / 确定性"工具开启 (默认白名单, config / env 可覆盖).
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

    def register_tool(self, name: str, tool_func: Callable,
                      description: str, input_schema: Dict) -> bool:
        """注册工具（实现 BaseAgent.register_tool 契约）。

        description 与 input_schema 当前未使用，预留给后续 LLM 驱动决策时
        作为工具元信息传给规划器（见 Task #9 计划）。
        """
        if self.tool_registry is None:
            self.tool_registry = {}
        if hasattr(self.tool_registry, "register"):
            self.tool_registry.register(name, tool_func)
        else:
            self.tool_registry[name] = tool_func
        return True

    def run_stream(self, request: Dict[str, Any]):
        """Agent 流式 generator (Task #48): yield ReAct 每一步 + final answer.

        Event 类型 (新增):
          {type: 'thought',     iteration, text}              LLM 思考
          {type: 'action',      iteration, tool_name, input}  即将执行的工具
          {type: 'observation', iteration, tool_name, success, output_summary}  工具返回
          {type: 'chunk',       text}                          final answer 增量 (跟 RAG 一样)
          {type: 'meta',        steps, tool_results_summary}    汇总
          {type: 'done',        cost_time, code}
          {type: 'error',       code, message}

        实现策略:
          - 仅 execution_strategy == 'react' 时走真实流; 'single_shot' 降级 execute + 一次性
          - 在 ReAct 循环里, 每个 thought/action/observation 立刻 yield 给 WS
          - final answer 不再走 chat_stream (Agent 不一定有专门的合成步),
            而是把 last_observation 当作 final answer 切片 yield 给前端
        """
        start_time = time.time()
        task = request.get("task")
        trace_id = request.get("trace_id")
        session_id = request.get("session_id")
        extra_params = request.get("extra_params") or {}

        if self.execution_strategy != "react":
            # single_shot 不支持真实流, 直接降级 execute + 一次性 chunk
            result = self.execute(dict(request))
            if result.get("code") == "SUCCESS":
                data = result.get("data") or {}
                yield {
                    "type": "meta",
                    "steps": data.get("steps") or [],
                    "tool_results_summary": data.get("tool_results_summary") or [],
                    "citations": data.get("citations") or [],
                    "retrieved_chunks": data.get("retrieved_chunks") or [],
                }
                ans = data.get("answer") or ""
                if ans:
                    yield {"type": "chunk", "text": ans}
                yield {"type": "done", "code": "SUCCESS",
                       "cost_time": round(time.time() - start_time, 3)}
            else:
                yield {
                    "type": "error",
                    "code": result.get("code", "AGENT_RUN_FAILED"),
                    "message": result.get("message", ""),
                }
            return

        # ============ ReAct 真实流式 ============
        llm_call = self._resolve_llm_planner(trace_id=trace_id)
        available_tools = self._available_tool_names()
        if llm_call is None or not available_tools:
            yield {"type": "error", "code": "AGENT_RUN_FAILED",
                   "message": "LLM 或 tool_registry 不可用"}
            return

        # Task V (#56) 流式版: plan_only=true → 跑一遍拿 plan, yield 给前端就停
        plan_only = bool(extra_params.get("plan_only", False)) and not bool(
            extra_params.get("approve_plan", False)
        )
        if plan_only:
            plan_result = self._generate_plan(
                task=task, available_tools=available_tools, trace_id=trace_id,
                llm_call=llm_call, tool_descriptions=self._tool_descriptions(),
            )
            if plan_result is not None:
                yield {
                    "type": "plan",
                    "plan": plan_result,
                    "available_tools": available_tools,
                }
                yield {
                    "type": "done",
                    "code": "PLAN_PENDING",
                    "cost_time": round(time.time() - start_time, 3),
                    "message": "请审批后带 approve_plan=true 重提.",
                }
                return
            # plan 失败 → 继续走完整 ReAct

        history: List[Dict[str, Any]] = []
        tool_results: List[Dict[str, Any]] = []
        final_answer: Optional[str] = None
        last_observation: Optional[Dict[str, Any]] = None
        tool_descriptions = self._tool_descriptions()

        try:
            for iteration in range(1, self.max_react_iterations + 1):
                prompt = self._build_react_prompt(
                    task=task, available_tools=available_tools,
                    history=history, iteration=iteration,
                    max_iterations=self.max_react_iterations,
                    tool_descriptions=tool_descriptions,
                )
                try:
                    raw = llm_call(prompt)
                except Exception as e:
                    yield {"type": "error", "code": "AGENT_RUN_FAILED",
                           "message": f"LLM 调用异常 iter={iteration}: {e}"}
                    return

                step = self._parse_react_response(raw, available_tools)
                if step is None:
                    yield {"type": "error", "code": "AGENT_RUN_FAILED",
                           "message": f"LLM 输出无法解析 iter={iteration}"}
                    return

                thought = step.get("thought", "")
                # 即时 yield thought 给前端
                yield {
                    "type": "thought",
                    "iteration": iteration,
                    "text": thought,
                }

                # 终止条件: LLM 输出 final_answer
                if "final_answer" in step:
                    final_answer = str(step["final_answer"])
                    history.append({"thought": thought, "final_answer": final_answer})
                    break

                # 执行 action
                action = step.get("action") or {}
                tool_name = action.get("tool")
                tool_input = action.get("input") or {}
                tool_input.setdefault("trace_id", trace_id)
                tool_input.setdefault("session_id", session_id)
                tool_input.setdefault("extra_params", extra_params)

                # 即时 yield action
                yield {
                    "type": "action",
                    "iteration": iteration,
                    "tool_name": tool_name,
                    "input": {k: v for k, v in tool_input.items()
                              if k not in ("trace_id", "session_id", "extra_params")},
                }

                # Task W (#57): 危险工具审批门槛 (流式版同样守护)
                if self._needs_approval(tool_name, extra_params):
                    yield {
                        "type": "error",
                        "code": "TOOL_APPROVAL_REQUIRED",
                        "message": f"工具 '{tool_name}' 需要审批; 请带 extra_params.approve_tools=['{tool_name}'] 重提.",
                        "tool_name": tool_name,
                        "required_tools": sorted(self.tool_approval_required),
                    }
                    return

                tool_result = self._call_tool_with_retry(
                    step={
                        "step_id": f"react_{iteration}",
                        "tool_name": tool_name,
                        "input_data": tool_input,
                    },
                    session_id=session_id, trace_id=trace_id,
                    max_retries=self.max_retries,
                )
                tool_results.append(tool_result)
                observation = self._summarize_tool_output(tool_result.get("output"))
                last_observation = tool_result
                history.append({
                    "thought": thought,
                    "action": {"tool": tool_name, "input": tool_input},
                    "observation": observation,
                })

                # 即时 yield observation
                yield {
                    "type": "observation",
                    "iteration": iteration,
                    "tool_name": tool_name,
                    "success": tool_result.get("success", False),
                    "output_summary": observation,
                }

            # ============ 循环结束: 取 final_answer ============
            if final_answer is None and last_observation is not None:
                output = last_observation.get("output") or {}
                if isinstance(output, dict):
                    data = output.get("data") or {}
                    final_answer = (data.get("answer") or data.get("text")
                                    or data.get("description")
                                    or self._summarize_tool_output(output))
                else:
                    final_answer = str(output)
            if not final_answer:
                final_answer = "ReAct 循环未产出可用答案"

            # yield meta + final answer chunks + done
            yield {
                "type": "meta",
                "steps": [
                    {"step_id": tr.get("step_id"), "tool_name": tr.get("tool_name"),
                     "success": tr.get("success", False)}
                    for tr in tool_results
                ],
                "tool_results_summary": [
                    {"tool_name": tr.get("tool_name"),
                     "summary": self._summarize_tool_output(tr.get("output"))}
                    for tr in tool_results
                ],
                "citations": [], "retrieved_chunks": [],
            }
            # Agent final answer 切片 (不调 chat_stream, 因为 final_answer 已经是字符串)
            if final_answer:
                chunk_size = max(1, len(final_answer) // 80)
                for i in range(0, len(final_answer), chunk_size):
                    yield {"type": "chunk", "text": final_answer[i:i + chunk_size]}

            yield {
                "type": "done",
                "code": "SUCCESS",
                "cost_time": round(time.time() - start_time, 3),
                "iterations_used": len(history),
            }
        except Exception as e:
            self.logger.error(f"[react-stream] 异常: {e}, trace_id={trace_id}")
            yield {"type": "error", "code": "UNKNOWN_ERROR", "message": str(e)}

    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """执行 Agent 任务，统一接收标准 request dict。

        策略分发(由 self.execution_strategy 控制):
            - "single_shot" (默认): parse_task 一次性输出全部 steps -> 顺序执行 -> 聚合
            - "react": 多轮 observe-reflect-next 循环, 每步 LLM 决定下一动作

        ReAct 模式失败/不可用时优雅降级到 single_shot, 主链路永远有响应。
        """
        start_time = time.time()

        task = request.get("task")
        trace_id = request.get("trace_id")
        extra_params = request.get("extra_params") or {}

        # 契约: session_id 应由 RequestHandler._standardize_request 统一补齐
        #       trace_id  应由 ApiService middleware / ConsoleApp 应用层入口生成
        # 这里只做"漏洞兜底": 如果发现上游没补齐,记录 ERROR 让漏洞冒头,而非静默修复
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
        # 单次请求可通过 extra_params.execution_strategy 覆盖全局设置
        strategy = extra_params.get("execution_strategy", self.execution_strategy)

        # ReAct 模式优先(hybrid 由文档定义为固定流水线,不走 ReAct)
        if strategy == "react" and execution_mode != "hybrid":
            react_result = self._react_execute(
                task=task,
                session_id=session_id,
                trace_id=trace_id,
                extra_params=extra_params,
                start_time=start_time,
            )
            if react_result is not None:
                return react_result
            self.logger.warning(
                f"[react] 多轮规划不可用(无 LLM 通道/任务不适合),fallback 到 single_shot: trace_id={trace_id}"
            )

        try:
            self.logger.info(
                f"Agent执行开始：session_id={session_id}, trace_id={trace_id}, mode={execution_mode}, strategy={strategy}"
            )

            self._append_state_event(
                session_id=session_id,
                event_type="task_started",
                trace_id=trace_id,
                payload={
                    "task": task,
                    "execution_mode": execution_mode,
                    "execution_strategy": strategy,
                    "timeout": timeout,
                    "max_retries": max_retries,
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
                    task=task,
                    session_id=session_id,
                    trace_id=trace_id,
                    extra_params=extra_params,
                )
                span.set_attribute("agent.plan_source", str(plan.get("plan_source", "?")))
                span.set_attribute("agent.steps_count", len(plan.get("steps", [])))

            self._append_state_event(
                session_id=session_id,
                event_type="task_parsed",
                trace_id=trace_id,
                payload={
                    "task": task,
                    "step_count": len(plan.get("steps", [])),
                },
            )

            tool_results = []
            for step in plan.get("steps", []):
                step_id = step.get("step_id")
                tool_name = step.get("tool_name")

                self._append_state_event(
                    session_id=session_id,
                    event_type="step_started",
                    trace_id=trace_id,
                    payload={
                        "step_id": step_id,
                        "tool_name": tool_name,
                    },
                )

                result = self._call_tool_with_retry(
                    step=step,
                    session_id=session_id,
                    trace_id=trace_id,
                    max_retries=max_retries,
                )
                tool_results.append(result)

                if result.get("success"):
                    self._append_state_event(
                        session_id=session_id,
                        event_type="step_finished",
                        trace_id=trace_id,
                        payload={
                            "step_id": step_id,
                            "tool_name": tool_name,
                        },
                    )
                else:
                    self._append_state_event(
                        session_id=session_id,
                        event_type="step_failed",
                        trace_id=trace_id,
                        payload={
                            "step_id": step_id,
                            "tool_name": tool_name,
                            "error": result.get("error"),
                        },
                    )

            aggregated = self.aggregate_results(
                task=task,
                session_id=session_id,
                trace_id=trace_id,
                tool_results=tool_results,
                execution_mode=execution_mode,
            )

            self._append_state_event(
                session_id=session_id,
                event_type="task_completed",
                trace_id=trace_id,
                payload={
                    "success_steps": len([r for r in tool_results if r.get("success")]),
                    "failed_steps": len([r for r in tool_results if not r.get("success")]),
                },
            )

            if self.state_store is not None:
                self._save_state_safe(
                    session_id=session_id,
                    state={
                        "status": "completed",
                        "task": task,
                        "execution_mode": execution_mode,
                        "updated_at": time.time(),
                    },
                    trace_id=trace_id,
                )

            return {
                "code": "SUCCESS",
                "message": "ok",
                "data": aggregated,
                "trace_id": trace_id,
                "retryable": False,
                "details": None,
                "cost_time": round(time.time() - start_time, 3),
            }

        except Exception as e:
            self.logger.error(f"Agent执行异常：{str(e)}, trace_id={trace_id}, session_id={session_id}")

            self._append_state_event(
                session_id=session_id,
                event_type="task_failed",
                trace_id=trace_id,
                payload={"error": str(e)},
            )

            return self._handle_exception(
                exception=e,
                trace_id=trace_id,
                session_id=session_id,
                task=task,
            )

    def parse_task(
            self,
            task: str,
            session_id: str,
            trace_id: Optional[str] = None,
            extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """解析任务并生成执行计划。

        策略:
            1. 若 use_llm_planner=True 且能拿到 LLM 调用通道 -> 让 LLM 规划
            2. LLM 规划失败 / JSON 解析失败 / 工具不在注册表 -> fallback 规则式
            3. hybrid 模式或没有 LLM 通道 -> 直接走规则式

        返回结构:
            {"session_id", "task", "steps", "plan_source": "llm"|"rule_based"}
        """
        extra_params = extra_params or {}
        execution_mode = extra_params.get("execution_mode", self.default_execution_mode)

        # 优先 LLM 规划(仅 agent 模式;hybrid 由文档定义为固定 rag + llm 两步)
        if self.use_llm_planner and execution_mode != "hybrid":
            llm_steps = self._llm_plan_task(
                task=task,
                trace_id=trace_id,
                extra_params=extra_params,
            )
            if llm_steps:
                return {
                    "session_id": session_id,
                    "task": task,
                    "steps": llm_steps,
                    "plan_source": "llm",
                }

        # Fallback: 规则式规划
        steps = self._rule_based_plan_task(
            task=task,
            execution_mode=execution_mode,
            trace_id=trace_id,
            extra_params=extra_params,
        )
        return {
            "session_id": session_id,
            "task": task,
            "steps": steps,
            "plan_source": "rule_based",
        }

    # =========================
    # 规划: ReAct 多轮循环
    # =========================
    def _react_execute(
            self,
            task: str,
            session_id: str,
            trace_id: Optional[str],
            extra_params: Dict[str, Any],
            start_time: float,
    ) -> Optional[Dict[str, Any]]:
        """ReAct 多轮循环: observe -> reflect -> next, 直到 final_answer 或 max iterations。

        返回:
            统一响应信封 dict (完整结果),或 None 表示"无法走 ReAct"(由 execute 降级到 single_shot)
        """
        llm_call = self._resolve_llm_planner(trace_id=trace_id)
        if llm_call is None:
            return None
        available_tools = self._available_tool_names()
        if not available_tools:
            return None

        # ============ Task V (#56): Plan mode 早出 ============
        # extra_params.plan_only=True 时, 跑 ReAct 第 1 轮拿到 plan (LLM 的
        # thought + action 或 final_answer), 不执行 action 直接返回. code=PLAN_PENDING.
        # 用户带 extra_params.approve_plan=true 再次提交则走完整 ReAct.
        plan_only = bool(extra_params.get("plan_only", False)) and not bool(
            extra_params.get("approve_plan", False)
        )
        if plan_only:
            plan_result = self._generate_plan(
                task=task, available_tools=available_tools, trace_id=trace_id,
                llm_call=llm_call, tool_descriptions=self._tool_descriptions(),
            )
            if plan_result is None:
                # plan 生成失败, 降级到正常 ReAct
                pass
            else:
                self._append_state_event(
                    session_id=session_id, event_type="plan_generated", trace_id=trace_id,
                    payload={"plan": plan_result},
                )
                return {
                    "code": "PLAN_PENDING",
                    "message": "已生成执行计划; 用户审批后带 extra_params.approve_plan=true 重新提交以执行.",
                    "data": {
                        "plan": plan_result,
                        "available_tools": available_tools,
                        "answer": "",
                        "citations": [],
                        "retrieved_chunks": [],
                        "steps": [],
                        "tool_results_summary": [],
                    },
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": {"plan_only": True},
                    "cost_time": round(time.time() - start_time, 3),
                }

        self._append_state_event(
            session_id=session_id, event_type="react_started", trace_id=trace_id,
            payload={"task": task, "max_iterations": self.max_react_iterations},
        )

        history: List[Dict[str, Any]] = []  # 每项 {thought, action?, observation?, final_answer?}
        tool_results: List[Dict[str, Any]] = []
        final_answer: Optional[str] = None
        last_observation: Optional[Dict[str, Any]] = None

        # registry 提供的工具描述, 给 LLM 看 (循环外读一次)
        tool_descriptions = self._tool_descriptions()

        for iteration in range(1, self.max_react_iterations + 1):
            prompt = self._build_react_prompt(
                task=task,
                available_tools=available_tools,
                history=history,
                iteration=iteration,
                max_iterations=self.max_react_iterations,
                tool_descriptions=tool_descriptions,
            )
            try:
                raw = llm_call(prompt)
            except Exception as e:
                self.logger.warning(f"[react] LLM 调用异常 iter={iteration}: {e}")
                return None  # 完全不可用 -> 降级 single_shot

            step = self._parse_react_response(raw, available_tools)
            if step is None:
                self.logger.warning(f"[react] LLM 输出无法解析 iter={iteration}, fallback")
                return None

            thought = step.get("thought", "")
            self._append_state_event(
                session_id=session_id, event_type="react_thought", trace_id=trace_id,
                payload={"iteration": iteration, "thought": thought[:200]},
            )

            # 终止条件: LLM 输出 final_answer
            if "final_answer" in step:
                final_answer = str(step["final_answer"])
                history.append({"thought": thought, "final_answer": final_answer})
                self._append_state_event(
                    session_id=session_id, event_type="react_final", trace_id=trace_id,
                    payload={"iteration": iteration, "final_answer_preview": final_answer[:200]},
                )
                break

            # 执行 action
            action = step.get("action") or {}
            tool_name = action.get("tool")
            tool_input = action.get("input") or {}
            tool_input.setdefault("trace_id", trace_id)
            tool_input.setdefault("session_id", session_id)
            tool_input.setdefault("extra_params", extra_params)

            # Task W (#57): 危险工具审批门槛
            if self._needs_approval(tool_name, extra_params):
                self.logger.warning(
                    f"[react] tool '{tool_name}' 需要审批但未通过, 中断: trace_id={trace_id}"
                )
                self._append_state_event(
                    session_id=session_id, event_type="react_approval_required",
                    trace_id=trace_id,
                    payload={"iteration": iteration, "tool_name": tool_name},
                )
                return {
                    "code": "TOOL_APPROVAL_REQUIRED",
                    "message": f"工具 '{tool_name}' 需要审批; 请带 extra_params.approve_tools=['{tool_name}', ...] 重新提交.",
                    "data": {
                        "tool_name": tool_name,
                        "tool_input": {
                            k: v for k, v in tool_input.items()
                            if k not in ("trace_id", "session_id", "extra_params")
                        },
                        "required_tools": sorted(self.tool_approval_required),
                        "approved_so_far": list(extra_params.get("approve_tools") or []),
                        "answer": "",
                        "citations": [],
                        "retrieved_chunks": [],
                        "steps": [],
                        "tool_results_summary": [],
                    },
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": {"iteration": iteration},
                    "cost_time": round(time.time() - start_time, 3),
                }

            self._append_state_event(
                session_id=session_id, event_type="react_action", trace_id=trace_id,
                payload={"iteration": iteration, "tool_name": tool_name},
            )

            # Task Z (#60): pre_tool_call hook — 可拦截 / 改 input / 抛 BlockedError
            hook_ctx = {
                "trace_id": trace_id, "session_id": session_id,
                "iteration": iteration, "phase": "react",
            }
            try:
                new_input = get_hook_registry().fire(
                    "pre_tool_call", tool_name, tool_input, hook_ctx,
                )
                if isinstance(new_input, dict):
                    tool_input = new_input
            except BlockedError as be:
                self.logger.warning(
                    f"[react] hook 拒绝 tool '{tool_name}': code={be.code} msg={be.message}"
                )
                return {
                    "code": be.code,
                    "message": be.message,
                    "data": {
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "blocked_by_hook": True,
                        "answer": "", "citations": [], "retrieved_chunks": [],
                        "steps": [], "tool_results_summary": [],
                    },
                    "trace_id": trace_id, "retryable": False,
                    "details": be.details,
                    "cost_time": round(time.time() - start_time, 3),
                }

            tool_result = self._call_tool_with_retry(
                step={"step_id": f"react_{iteration}", "tool_name": tool_name, "input_data": tool_input},
                session_id=session_id,
                trace_id=trace_id,
                max_retries=self.max_retries,
            )

            # Task Z (#60): post_tool_call hook — 可看 / 改 result, 不能拦截
            try:
                new_result = get_hook_registry().fire(
                    "post_tool_call", tool_name, tool_input, tool_result, hook_ctx,
                )
                if isinstance(new_result, dict):
                    tool_result = new_result
            except BlockedError as be:
                # post hook 抛 BlockedError 也尊重 (主要给审计用)
                return {
                    "code": be.code,
                    "message": be.message,
                    "data": {"tool_name": tool_name, "blocked_by_hook": True, "answer": "",
                             "citations": [], "retrieved_chunks": [], "steps": [],
                             "tool_results_summary": []},
                    "trace_id": trace_id, "retryable": False,
                    "details": be.details,
                    "cost_time": round(time.time() - start_time, 3),
                }
            except Exception:
                pass  # post hook 其他异常吞掉, 继续

            tool_results.append(tool_result)
            observation = self._summarize_tool_output(tool_result.get("output"))
            last_observation = tool_result

            history.append({"thought": thought, "action": {"tool": tool_name, "input": tool_input}, "observation": observation})
            self._append_state_event(
                session_id=session_id, event_type="react_observation", trace_id=trace_id,
                payload={"iteration": iteration, "tool_name": tool_name, "success": tool_result.get("success"), "obs": observation[:200]},
            )

        # 循环结束: 整合最终结果
        if final_answer is None and last_observation is not None:
            # 没有输出 final_answer 就用最后观察作为答案
            output = last_observation.get("output") or {}
            if isinstance(output, dict):
                data = output.get("data") or {}
                final_answer = data.get("answer") or data.get("text") or self._summarize_tool_output(output)
            else:
                final_answer = str(output)
        if not final_answer:
            final_answer = "ReAct 循环未产出可用答案"

        self._append_state_event(
            session_id=session_id, event_type="react_completed", trace_id=trace_id,
            payload={"iterations_used": len(history), "had_final_answer": "final_answer" in history[-1] if history else False},
        )

        cost_time = round(time.time() - start_time, 3)
        return {
            "code": "SUCCESS",
            "message": "ok",
            "data": {
                "answer": final_answer,
                "session_id": session_id,
                "trace_id": trace_id,
                "execution_mode": "react",
                "execution_strategy": "react",
                "iterations_used": len(history),
                "react_history": [
                    {
                        "iteration": i + 1,
                        "thought": (h.get("thought") or "")[:200],
                        "action": h.get("action"),
                        "observation_preview": (h.get("observation") or "")[:200] if h.get("observation") else None,
                        "final_answer": h.get("final_answer"),
                    }
                    for i, h in enumerate(history)
                ],
                "tool_results_summary": [
                    {"tool_name": tr.get("tool_name"), "success": tr.get("success")}
                    for tr in tool_results
                ],
                "citations": [],
                "retrieved_chunks": [],
            },
            "trace_id": trace_id,
            "retryable": False,
            "details": None,
            "cost_time": cost_time,
        }

    @staticmethod
    def _build_react_prompt(
            task: str,
            available_tools: List[str],
            history: List[Dict[str, Any]],
            iteration: int,
            max_iterations: int,
            tool_descriptions: Optional[Dict[str, str]] = None,
    ) -> str:
        """构造 ReAct prompt: 任务 + 工具 + 历史 + 期望输出格式

        tool_descriptions 是从 registry.describe_all() 拿到的, 优先级最高;
        缺失时退回内置 tool_docs (向后兼容 rag_search / llm_generate)。

        Task U (#55): 顶部注入 ProjectMemory (AGENTS.md / CLAUDE.md) 让 LLM
        知道项目约定/偏好/架构, 调工具风格更一致。
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
                # 从 wrapped 抽出 <Skills>...</Skills> 块
                end = wrapped.find("</Skills>")
                if end != -1:
                    skills_block = wrapped[: end + len("</Skills>")] + "\n\n"
        except Exception:
            skills_block = ""

        return (
            f"{memory_block}"
            f"{skills_block}"
            f"你是一个 ReAct 模式智能代理。当前任务: {task}\n"
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
    def _parse_react_response(
            raw: str,
            available_tools: List[str],
    ) -> Optional[Dict[str, Any]]:
        """从 LLM 输出抠出 JSON,校验结构合法性。"""
        if not raw or not isinstance(raw, str):
            return None
        candidate = raw.strip()
        if candidate.startswith("```"):
            candidate = re.sub(r"^```[a-zA-Z]*\n?", "", candidate)
            candidate = re.sub(r"```\s*$", "", candidate).strip()
        match = re.search(r"\{[\s\S]*\}", candidate)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None

        # 二选一: final_answer 或 action
        if "final_answer" in parsed:
            return {"thought": parsed.get("thought", ""), "final_answer": parsed["final_answer"]}

        action = parsed.get("action")
        if not isinstance(action, dict):
            return None
        tool_name = action.get("tool")
        if tool_name not in set(available_tools):
            return None
        return {
            "thought": parsed.get("thought", ""),
            "action": {
                "tool": tool_name,
                "input": action.get("input") or {},
            },
        }

    # =========================
    # Task W (#57): 工具审批门槛
    # =========================
    # =========================
    # Task FF (#66): 工具结果缓存 LRU
    # =========================
    def _tool_cache_key(self, tool_name: str, payload: Dict[str, Any]) -> str:
        """生成 (tool_name + 排序后 input) 的稳定 hash key.

        会刻意排除 trace_id / session_id / extra_params 等 'transient' 字段, 让
        同 query 不同 trace_id 也能命中缓存 (它们对工具输出不影响).
        """
        transient = {"trace_id", "session_id", "extra_params"}
        normalized = {k: v for k, v in payload.items() if k not in transient}
        # 排序 + json 序列化保证稳定 key
        try:
            key_str = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            # 非 json-serializable -> 用 repr 兜底 (虽然不够稳但不会崩)
            key_str = repr(sorted(normalized.items()))
        h = hashlib.md5(f"{tool_name}|{key_str}".encode("utf-8")).hexdigest()
        return f"{tool_name}:{h}"

    def _tool_cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        """LRU 读: 命中后移到队尾 (最近用过). 未命中返回 None."""
        with self._tool_cache_lock:
            val = self._tool_cache.get(key)
            if val is None:
                self._tool_cache_stats["misses"] += 1
                return None
            # move to end (最近用过)
            self._tool_cache.move_to_end(key)
            self._tool_cache_stats["hits"] += 1
            return val

    def _tool_cache_put(self, key: str, value: Dict[str, Any]) -> None:
        """LRU 写: 超过 max_size 删除最旧."""
        if self.tool_cache_max_size <= 0:
            return
        with self._tool_cache_lock:
            self._tool_cache[key] = value
            self._tool_cache.move_to_end(key)
            while len(self._tool_cache) > self.tool_cache_max_size:
                self._tool_cache.popitem(last=False)

    def tool_cache_stats(self) -> Dict[str, Any]:
        """给 /admin/status 看用."""
        with self._tool_cache_lock:
            stats = dict(self._tool_cache_stats)
            total = stats["hits"] + stats["misses"]
            stats["hit_ratio"] = round(stats["hits"] / total, 3) if total else 0.0
            stats["size"] = len(self._tool_cache)
            stats["max_size"] = self.tool_cache_max_size
            stats["cacheable_tools"] = sorted(self.cacheable_tools)
            return stats

    def clear_tool_cache(self) -> None:
        """主要给测试用."""
        with self._tool_cache_lock:
            self._tool_cache.clear()
            self._tool_cache_stats = {"hits": 0, "misses": 0}

    def _needs_approval(self, tool_name: Optional[str], extra_params: Dict[str, Any]) -> bool:
        """判断该工具是否需要审批但未通过.

        返回 True = 该工具在 tool_approval_required 名单且 extra_params.approve_tools
                    不包含它 → 应该中断, 返回 TOOL_APPROVAL_REQUIRED.
        返回 False = 工具不在名单, 或已被 approve, 或工具名为空 → 正常执行.
        """
        if not tool_name:
            return False
        if tool_name not in self.tool_approval_required:
            return False
        approved = extra_params.get("approve_tools") or []
        if isinstance(approved, str):
            approved = [approved]
        if "*" in approved or tool_name in approved:
            return False
        return True

    # =========================
    # Task V (#56): Plan mode — 一次性 LLM 调用产 plan, 不执行 action
    # =========================
    def _generate_plan(
            self,
            task: str,
            available_tools: List[str],
            trace_id: Optional[str],
            llm_call: Callable[[str], str],
            tool_descriptions: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """跑一次 ReAct 风格的 LLM 调用, 拿到 plan (thought + action 或 final_answer),
        不执行任何工具. 失败返回 None.

        plan 结构:
            {thought: str, action: {tool, input}}   # 准备调工具
          或
            {thought: str, final_answer: str}        # LLM 觉得不需要调工具

        前端可用这份 plan 给用户预览 + 审批; 审批通过后带 approve_plan=true 重提.
        """
        prompt = self._build_react_prompt(
            task=task,
            available_tools=available_tools,
            history=[],
            iteration=1,
            max_iterations=self.max_react_iterations,
            tool_descriptions=tool_descriptions or {},
        )
        try:
            raw = llm_call(prompt)
        except Exception as e:
            self.logger.warning(f"[plan-mode] LLM 调用异常: trace_id={trace_id}, err={e}")
            return None
        step = self._parse_react_response(raw, available_tools)
        if step is None:
            self.logger.warning(f"[plan-mode] LLM 输出无法解析: trace_id={trace_id}")
            return None
        # 返回结构化 plan + 摘要文本 (给前端展示)
        summary_parts = [f"💭 思考: {step.get('thought', '')}"]
        if "final_answer" in step:
            summary_parts.append(f"🎯 直接答: {step['final_answer'][:200]}")
        elif "action" in step:
            a = step["action"]
            summary_parts.append(f"🔧 拟调: {a.get('tool')} {json.dumps(a.get('input') or {}, ensure_ascii=False)[:100]}")
        return {
            **step,
            "summary": "\n".join(summary_parts),
        }

    # =========================
    # 规划: LLM 驱动版本
    # =========================
    def _llm_plan_task(
            self,
            task: str,
            trace_id: Optional[str],
            extra_params: Dict[str, Any],
    ) -> Optional[List[Dict[str, Any]]]:
        """让 LLM 输出 JSON 格式的执行计划; 任一环节失败返回 None 触发 fallback。"""
        # 1. 拿可调用的 llm 通道
        llm_call = self._resolve_llm_planner(trace_id=trace_id)
        if llm_call is None:
            return None

        # 2. 列出 tool_registry 中实际可用的工具
        available_tools = self._available_tool_names()
        if not available_tools:
            return None

        # 3. 构造 prompt (带 registry 提供的工具描述, 给 LLM 看)
        prompt = self._build_planner_prompt(
            task=task,
            available_tools=available_tools,
            tool_descriptions=self._tool_descriptions(),
        )

        # 4. 调用 LLM
        try:
            raw = llm_call(prompt)
        except Exception as e:
            self.logger.warning(f"[planner] LLM 规划调用异常,fallback 规则式: {e}")
            return None

        # 5. 解析 JSON 与校验
        plan_steps = self._parse_planner_response(raw=raw, available_tools=available_tools)
        if not plan_steps:
            return None

        # 6. 补全 step 字段(trace_id / extra_params)
        steps: List[Dict[str, Any]] = []
        for i, step in enumerate(plan_steps[: self.max_planner_steps], start=1):
            input_data = step.get("input_data") or {}
            input_data.setdefault("trace_id", trace_id)
            input_data.setdefault("extra_params", extra_params)
            # rag_search 默认 top_k
            if step.get("tool_name") == "rag_search":
                input_data.setdefault("top_k", extra_params.get("top_k", 5))
                input_data.setdefault("query", task)
            # llm_generate 默认 prompt 用 task
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

    def _resolve_llm_planner(self, trace_id: Optional[str]) -> Optional[Callable[[str], str]]:
        """优先用显式注入的 llm_planner;否则通过 tool_registry 的 llm_generate 调 LLM。"""
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
                # llm_generate 工具协议: data.text 为文本回复
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
        """从 tool_registry 抽出每个工具的描述. registry 没暴露 describe* 时返回空 dict.

        旧的硬编码 fallback 仍由 _build_planner_prompt / _build_react_prompt 兜底,
        所以 registry 没描述也不会挂。
        """
        if self.tool_registry is None:
            return {}
        # 1. describe_all() 优先 (返回完整 dict)
        if hasattr(self.tool_registry, "describe_all"):
            try:
                out = self.tool_registry.describe_all()
                if isinstance(out, dict):
                    return {str(k): str(v) for k, v in out.items()}
            except Exception:
                pass
        # 2. 每个工具单独问 describe(name)
        if hasattr(self.tool_registry, "describe"):
            names = self._available_tool_names()
            try:
                return {n: str(self.tool_registry.describe(n) or "") for n in names}
            except Exception:
                pass
        return {}

    @staticmethod
    def _build_planner_prompt(
        task: str,
        available_tools: List[str],
        tool_descriptions: Optional[Dict[str, str]] = None,
    ) -> str:
        """构造 LLM 规划 prompt。

        tool_descriptions: 从 registry 取的描述, 优先级最高;
                           缺失时 fall back 到内置 tool_docs (向后兼容)。
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
        """从 LLM 返回中提取 JSON steps 列表,校验工具合法性。"""
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

    # =========================
    # 规划: 规则式 fallback
    # =========================
    def _rule_based_plan_task(
            self,
            task: str,
            execution_mode: str,
            trace_id: Optional[str],
            extra_params: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """规则式规划(LLM 不可用时的 fallback)。"""
        steps: List[Dict[str, Any]] = []

        if execution_mode == "hybrid":
            steps.append({
                "step_id": "s1",
                "tool_name": "rag_search",
                "description": "先做知识库检索",
                "input_data": {
                    "query": task,
                    "top_k": extra_params.get("top_k", 5),
                    "trace_id": trace_id,
                    "extra_params": extra_params,
                },
            })
            steps.append({
                "step_id": "s2",
                "tool_name": "llm_generate",
                "description": "基于检索结果生成总结",
                "input_data": {
                    "prompt": task,
                    "trace_id": trace_id,
                    "extra_params": extra_params,
                },
            })
        else:
            steps.append({
                "step_id": "s1",
                "tool_name": "llm_generate",
                "description": "执行通用文本生成",
                "input_data": {
                    "prompt": task,
                    "trace_id": trace_id,
                    "extra_params": extra_params,
                },
            })

        return steps

    def aggregate_results(
            self,
            task: str,
            session_id: str,
            trace_id: Optional[str],
            tool_results: List[Dict[str, Any]],
            execution_mode: str,
    ) -> Dict[str, Any]:
        """聚合工具结果，输出统一结构"""

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

            steps.append(
                {
                    "step_id": item.get("step_id"),
                    "tool_name": tool_name,
                    "success": success,
                }
            )

            if not success:
                summaries.append(
                    {
                        "tool_name": tool_name,
                        "summary": f"失败：{item.get('error', 'unknown error')}",
                    }
                )
                continue

            # 统一提取 output.data
            data = output.get("data") if isinstance(output, dict) else None

            # rag_search：只提取结构化结果，不把整段回答直接拼到最终 answer
            if tool_name == "rag_search":
                if isinstance(data, dict):
                    rag_answer = data.get("answer", "") or ""
                    final_citations = data.get("citations", []) or []
                    final_retrieved_chunks = data.get("retrieved_chunks", []) or []

                summaries.append(
                    {
                        "tool_name": tool_name,
                        "summary": self._summarize_tool_output(output),
                    }
                )
                continue

            # llm_generate：作为 hybrid 的最终答案主来源
            if tool_name == "llm_generate":
                if isinstance(data, dict):
                    llm_answer = (
                            data.get("answer")
                            or data.get("text")
                            or data.get("content")
                            or ""
                    )
                else:
                    llm_answer = str(output)

                summaries.append(
                    {
                        "tool_name": tool_name,
                        "summary": self._summarize_tool_output(output),
                    }
                )
                continue

            # 其他工具：保留摘要
            summaries.append(
                {
                    "tool_name": tool_name,
                    "summary": self._summarize_tool_output(output),
                }
            )

        # 根据模式决定最终 answer
        if execution_mode == "hybrid":
            # hybrid：优先 llm_generate，其次 rag_answer
            final_answer = llm_answer or rag_answer or "任务执行完成，但未生成可展示内容。"
        else:
            # agent：优先 llm_generate，其次回退到首个成功结果摘要
            final_answer = llm_answer or rag_answer
            if not final_answer:
                success_summaries = [x["summary"] for x in summaries if not x["summary"].startswith("失败：")]
                final_answer = success_summaries[0] if success_summaries else "任务执行完成，但未生成可展示内容。"

        return {
            "answer": final_answer,
            "session_id": session_id,
            "trace_id": trace_id,
            "execution_mode": execution_mode,
            "steps": steps,
            "tool_results_summary": summaries,
            "citations": final_citations,
            "retrieved_chunks": final_retrieved_chunks,
        }

    def _call_tool_with_retry(
            self,
            step: Dict[str, Any],
            session_id: str,
            trace_id: Optional[str],
            max_retries: int,
    ) -> Dict[str, Any]:
        """按重试策略调用工具. Task FF (#66): 命中缓存直接返回, 减少重复触发."""
        step_id = step.get("step_id")
        tool_name = step.get("tool_name")
        payload = dict(step.get("input_data") or {})
        payload.setdefault("session_id", session_id)
        payload.setdefault("trace_id", trace_id)

        tool = self._get_tool(tool_name)
        if tool is None:
            return {
                "step_id": step_id,
                "tool_name": tool_name,
                "success": False,
                "output": None,
                "error": f"工具不存在：{tool_name}",
                "code": "TOOL_NOT_FOUND",
            }

        # Task FF (#66): 缓存查询 — 仅对白名单工具有效
        cache_key = None
        if tool_name in self.cacheable_tools:
            cache_key = self._tool_cache_key(tool_name, payload)
            cached = self._tool_cache_get(cache_key)
            if cached is not None:
                # 返回缓存的 result, 但补回 step_id (每次调用都该不同)
                cached_copy = dict(cached)
                cached_copy["step_id"] = step_id
                cached_copy["_cache_hit"] = True
                return cached_copy

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                output = tool(payload)

                is_success = True
                if isinstance(output, dict):
                    code = output.get("code")
                    if code and code != "SUCCESS":
                        is_success = False

                result = {
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "success": is_success,
                    "output": output,
                    "error": None if is_success else (
                        output.get("message", "tool returned non-success code")
                        if isinstance(output, dict) else "tool returned non-success result"
                    ),
                    "attempt": attempt + 1,
                }
                # 仅缓存成功的结果 (失败不缓存, 避免把暂时故障锁住)
                if cache_key and is_success:
                    self._tool_cache_put(cache_key, result)
                return result
            except Exception as e:
                last_error = str(e)
                self.logger.warning(
                    f"工具调用失败：tool={tool_name}, attempt={attempt + 1}, trace_id={trace_id}, error={last_error}"
                )

        return {
            "step_id": step_id,
            "tool_name": tool_name,
            "success": False,
            "output": None,
            "error": last_error or "tool call failed",
            "code": "TOOL_CALL_FAILED",
            "attempt": max_retries + 1,
        }

    def _get_tool(self, name: str):
        """从注册表获取工具，兼容 dict 和 ToolRegistry 两种风格"""
        if self.tool_registry is None:
            return None
        if hasattr(self.tool_registry, "get"):
            return self.tool_registry.get(name)
        if isinstance(self.tool_registry, dict):
            return self.tool_registry.get(name)
        return None

    def _append_state_event(
            self,
            session_id: str,
            event_type: str,
            trace_id: Optional[str],
            payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """安全写入状态事件：失败不阻断主任务"""
        if self.state_store is None:
            return

        event = {
            "session_id": session_id,
            "event_type": event_type,
            "trace_id": trace_id,
            "payload": payload or {},
            "created_at": time.time(),
        }

        try:
            if hasattr(self.state_store, "append_event"):
                self.state_store.append_event(session_id, event)
        except Exception as e:
            self.logger.warning(
                f"状态事件写入失败（已忽略）：session_id={session_id}, event_type={event_type}, error={str(e)}"
            )

    def _save_state_safe(
            self,
            session_id: str,
            state: Dict[str, Any],
            trace_id: Optional[str],
    ) -> None:
        """安全保存聚合状态：失败不阻断主任务"""
        if self.state_store is None:
            return
        try:
            if hasattr(self.state_store, "save_state"):
                self.state_store.save_state(session_id, state)
        except Exception as e:
            self.logger.warning(
                f"状态保存失败（已忽略）：session_id={session_id}, trace_id={trace_id}, error={str(e)}"
            )

    def _fallback_session_id(self) -> str:
        """仅兼容兜底使用，不作为主路径"""
        return f"{self.session_prefix}_{uuid.uuid4().hex[:12]}"

    def _summarize_tool_output(self, output: Any) -> str:
        """将工具输出压缩为简短摘要 (给 LLM 看 + 给前端展示).

        长度上限 2000 — 给前端足够空间展示完整 description / answer,
        同时不让 LLM 上下文爆掉 (2000 char ~ 600 token, 远低于上下文窗口)。
        优先级: data.description > data.answer > data.text > data.content > 顶层 answer/text/content > 原 dict 序列化
        """
        _LIMIT = 2000

        if isinstance(output, dict):
            if "data" in output and isinstance(output["data"], dict):
                data = output["data"]
                # image_describe / wikipedia 等工具用 description
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
                # 没有上面任何字段时, 把整个 data 序列化 (保留结构信息给 LLM)
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
        """统一异常处理(委托给 deps_module.handle_exception_to_envelope)。"""
        return handle_exception_to_envelope(
            exception_handler=self.exception_handler,
            exception=exception,
            trace_id=trace_id,
            fallback_code="AGENT_RUN_FAILED",
            fallback_message="Agent执行失败",
            stage="agent",
            context={"session_id": session_id, "task": task},
        )

    def call_agent(
            self,
            task: str,
            session_id: Optional[str] = None,
            trace_id: Optional[str] = None,
            extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        兼容旧抽象接口：将旧风格调用转成统一 request dict，再复用 execute()
        """
        request = {
            "type": "agent",
            "task": task,
            "trace_id": trace_id,
            "extra_params": extra_params or {},
        }

        if session_id:
            request["session_id"] = session_id

        return self.execute(request)

    def unregister_tool(self, name: str) -> bool:
        """
        注销工具，兼容 dict 和 ToolRegistry 两种风格
        """
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
