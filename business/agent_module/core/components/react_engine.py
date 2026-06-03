# -*- coding: utf-8 -*-
"""
ReActEngineMixin (Task KK #71)

ReAct 多轮循环 + Plan mode 早出 + LLM 输出解析:
    _react_execute               主循环 (observe → reflect → next, max_react_iterations)
    _generate_plan               Task V (#56) plan_only=true 时只输出 plan 不执行
    _parse_react_response        从 LLM 输出抠 JSON 校验

依赖 SimpleAgent (self) 字段:
    llm_planner, tool_registry, logger, max_react_iterations, max_retries,
    tool_approval_required, _resolve_llm_planner, _available_tool_names,
    _tool_descriptions, _build_react_prompt (mixin), _needs_approval (mixin),
    _call_tool_with_retry (mixin), _summarize_tool_output, _append_state_event
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional

from common_utils_module import BlockedError, get_hook_registry


class ReActEngineMixin:
    """ReAct 多轮规划循环 + Plan mode 早出."""

    # Task YY (#85): 走 DI 拿 hook_registry — 优先 deps 注入, fallback 全局单例.
    # 让单元测试可以注入隔离的 HookRegistry, 避免全局单例残留 hook 污染下条测试.
    def _hook_registry(self):
        deps = getattr(self, "deps", None)
        if deps is not None and getattr(deps, "hook_registry", None) is not None:
            return deps.hook_registry
        return get_hook_registry()

    def _react_execute(
            self,
            task: str,
            session_id: str,
            trace_id: Optional[str],
            extra_params: Dict[str, Any],
            start_time: float,
            timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """ReAct 多轮循环: observe -> reflect -> next, 直到 final_answer 或 max iterations.

        返回:
            统一响应信封 dict (完整结果), 或 None 表示"无法走 ReAct"(由 execute 降级 single_shot).
        """
        llm_call = self._resolve_llm_planner(trace_id=trace_id)
        if llm_call is None:
            return None
        available_tools = self._available_tool_names()
        if not available_tools:
            return None

        # Task PM-7-3: 前端 settings.currentSessionSysPrompt → extra_params.system_prompt
        # 这里在 task 顶部加一段 [本会话角色] 块, 跟 ProjectMemory (AGENTS.md 全局)
        # 叠加, 用户能 per-session 定制 LLM 行为而不污染项目级 memory.
        _session_sys = (extra_params.get("system_prompt") or "").strip()
        if _session_sys:
            task = f"[本会话角色 / SessionSystemPrompt]:\n{_session_sys}\n\n---\n\n{task}"

        # ============ Task V (#56): Plan mode 早出 ============
        plan_only = bool(extra_params.get("plan_only", False)) and not bool(
            extra_params.get("approve_plan", False)
        )
        if plan_only:
            plan_result = self._generate_plan(
                task=task, available_tools=available_tools, trace_id=trace_id,
                llm_call=llm_call, tool_descriptions=self._tool_descriptions(),
            )
            if plan_result is not None:
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

        # Task TTTT-1 (#138): 允许 extra_params.max_iterations 临时覆盖默认 self.max_react_iterations.
        # 限制 1~50 防爆掉成本.
        _override = extra_params.get("max_iterations")
        if isinstance(_override, int) and 1 <= _override <= 50:
            max_iter = _override
        else:
            max_iter = self.max_react_iterations

        self._append_state_event(
            session_id=session_id, event_type="react_started", trace_id=trace_id,
            payload={"task": task, "max_iterations": max_iter},
        )

        # AUDIT-2a: wall-clock 超时 enforce. doc/错误码表承诺 agent.timeout / AGENT_TIMEOUT,
        # 但历史上 timeout 只写进 state payload 从不生效, 长 ReAct 循环无超时兜底。
        effective_timeout = timeout if (timeout and timeout > 0) else getattr(self, "timeout", 0)
        timed_out = False

        history: List[Dict[str, Any]] = []
        tool_results: List[Dict[str, Any]] = []
        final_answer: Optional[str] = None
        last_observation: Optional[Dict[str, Any]] = None

        tool_descriptions = self._tool_descriptions()

        for iteration in range(1, max_iter + 1):
            # AUDIT-2a: 每轮入口检查 wall-clock; 超过 timeout 立即中止, 不再开新一轮 LLM+工具
            if effective_timeout and (time.time() - start_time) > effective_timeout:
                timed_out = True
                self._append_state_event(
                    session_id=session_id, event_type="react_timeout", trace_id=trace_id,
                    payload={"iteration": iteration,
                             "elapsed": round(time.time() - start_time, 3),
                             "timeout": effective_timeout},
                )
                break
            prompt = self._build_react_prompt(
                task=task,
                available_tools=available_tools,
                history=history,
                iteration=iteration,
                max_iterations=max_iter,
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

            # Task Z (#60): pre_tool_call hook
            hook_ctx = {
                "trace_id": trace_id, "session_id": session_id,
                "iteration": iteration, "phase": "react",
            }
            try:
                new_input = self._hook_registry().fire(
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

            # Task Z (#60): post_tool_call hook
            try:
                new_result = self._hook_registry().fire(
                    "post_tool_call", tool_name, tool_input, tool_result, hook_ctx,
                )
                if isinstance(new_result, dict):
                    tool_result = new_result
            except BlockedError as be:
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

            # Task MMM (#99): 保留结构化输出 (web_search 等返 results 数组的)
            # 让前端渲染 link card. 兜底为 None 不影响老前端.
            structured_output = None
            try:
                output = tool_result.get("output") or {}
                if isinstance(output, dict):
                    data = output.get("data")
                    if isinstance(data, dict):
                        structured_output = data
            except Exception:
                structured_output = None

            history.append({
                "thought": thought,
                "action": {"tool": tool_name, "input": tool_input},
                "observation": observation,
                # 给前端 web_search / json_query 等结构化工具结果用
                "observation_data": structured_output,
            })
            self._append_state_event(
                session_id=session_id, event_type="react_observation", trace_id=trace_id,
                payload={"iteration": iteration, "tool_name": tool_name, "success": tool_result.get("success"), "obs": observation[:200]},
            )

        # AUDIT-2a: 超时中止 → 返回 AGENT_TIMEOUT (带已完成的部分轨迹), 不伪装成 SUCCESS
        if timed_out and not final_answer:
            return {
                "code": "AGENT_TIMEOUT",
                "message": f"Agent ReAct 执行超过 {effective_timeout}s 超时中止 (已完成 {len(history)} 轮)",
                "data": {
                    "answer": "", "session_id": session_id, "trace_id": trace_id,
                    "execution_mode": "react", "execution_strategy": "react",
                    "iterations_used": len(history),
                    "tool_results_summary": [
                        {"tool_name": tr.get("tool_name"), "success": tr.get("success")}
                        for tr in tool_results
                    ],
                    "citations": [], "retrieved_chunks": [],
                },
                "trace_id": trace_id, "retryable": True,
                "details": {"timeout": effective_timeout, "iterations_used": len(history)},
                "cost_time": round(time.time() - start_time, 3),
            }

        # 循环结束: 整合最终结果
        if final_answer is None and last_observation is not None:
            output = last_observation.get("output") or {}
            if isinstance(output, dict):
                data = output.get("data") or {}
                final_answer = data.get("answer") or data.get("text") or self._summarize_tool_output(output)
            else:
                final_answer = str(output)
        if not final_answer:
            final_answer = "ReAct 循环未产出可用答案"
        elif not self._sanitize_final_answer(final_answer):
            # ZZ-6 (#122): final_answer 是裸工具名 (LLM 幻觉) → 兜底, 不把工具名吐给用户
            final_answer = "抱歉, 这次没能正确组织出答案, 换个说法再试一次。"

        self._append_state_event(
            session_id=session_id, event_type="react_completed", trace_id=trace_id,
            payload={"iterations_used": len(history),
                     "had_final_answer": "final_answer" in history[-1] if history else False},
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
                        # Task MMM (#99): 透传结构化输出给前端 (web_search link card 等)
                        "observation_data": h.get("observation_data"),
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
    def _parse_react_response(
            raw: str,
            available_tools: List[str],
    ) -> Optional[Dict[str, Any]]:
        """从 LLM 输出抠出 JSON, 校验结构合法性."""
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

    def _generate_plan(
            self,
            task: str,
            available_tools: List[str],
            trace_id: Optional[str],
            llm_call: Callable[[str], str],
            tool_descriptions: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Task V (#56): 跑一次 ReAct 风格 LLM 调用拿 plan, 不执行任何工具.

        plan 结构:
            {thought: str, action: {tool, input}}   # 准备调工具
          或
            {thought: str, final_answer: str}        # LLM 觉得不需要调工具
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
        # 摘要 (给前端展示用)
        summary_parts = [f"💭 思考: {step.get('thought', '')}"]
        if "final_answer" in step:
            summary_parts.append(f"🎯 直接答: {step['final_answer'][:200]}")
        elif "action" in step:
            a = step["action"]
            summary_parts.append(
                f"🔧 拟调: {a.get('tool')} "
                f"{json.dumps(a.get('input') or {}, ensure_ascii=False)[:100]}"
            )
        return {
            **step,
            "summary": "\n".join(summary_parts),
        }
