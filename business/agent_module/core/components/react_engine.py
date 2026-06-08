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

            # Phase3: 多动作并行 — actions 存在则一步并发执行 (相互独立的工具, 如同时查多源).
            # 审批门槛: 任一工具需审批且未批 → 整批阻断 (与单动作一致, 安全优先)。
            multi_actions = step.get("actions")
            if multi_actions:
                prepared: List[Any] = []
                blocked_tool = None
                for a in multi_actions:
                    tn = a.get("tool")
                    if self._needs_approval(tn, extra_params):
                        blocked_tool = tn
                        break
                    ti = dict(a.get("input") or {})
                    ti.setdefault("trace_id", trace_id)
                    ti.setdefault("session_id", session_id)
                    ti.setdefault("extra_params", extra_params)
                    prepared.append((tn, ti))
                if blocked_tool is not None:
                    self._append_state_event(
                        session_id=session_id, event_type="react_approval_required",
                        trace_id=trace_id,
                        payload={"iteration": iteration, "tool_name": blocked_tool, "parallel": True},
                    )
                    return {
                        "code": "TOOL_APPROVAL_REQUIRED",
                        "message": f"工具 '{blocked_tool}' 需要审批; 请带 extra_params.approve_tools=['{blocked_tool}', ...] 重新提交.",
                        "data": {
                            "tool_name": blocked_tool,
                            "required_tools": sorted(self.tool_approval_required),
                            "approved_so_far": list(extra_params.get("approve_tools") or []),
                            "answer": "", "citations": [], "retrieved_chunks": [],
                            "steps": [], "tool_results_summary": [],
                        },
                        "trace_id": trace_id, "retryable": False,
                        "details": {"iteration": iteration, "parallel": True},
                        "cost_time": round(time.time() - start_time, 3),
                    }
                self._append_state_event(
                    session_id=session_id, event_type="react_parallel_actions", trace_id=trace_id,
                    payload={"iteration": iteration, "tools": [tn for tn, _ in prepared]},
                )
                par_results = self._run_actions_parallel(prepared, session_id, trace_id, iteration)
                for (tn, ti), tr in zip(prepared, par_results):
                    tool_results.append(tr)
                    obs = self._summarize_tool_output(tr.get("output"))
                    last_observation = tr
                    sdata = None
                    try:
                        _out = tr.get("output") or {}
                        if isinstance(_out, dict) and isinstance(_out.get("data"), dict):
                            sdata = _out["data"]
                    except Exception:
                        sdata = None
                    history.append({
                        "thought": thought,
                        "action": {"tool": tn, "input": ti},
                        "observation": obs,
                        "observation_data": sdata,
                    })
                self._append_state_event(
                    session_id=session_id, event_type="react_parallel_done", trace_id=trace_id,
                    payload={"iteration": iteration,
                             "results": [{"tool_name": tr.get("tool_name"), "success": tr.get("success")}
                                         for tr in par_results]},
                )
                continue

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

    def _run_actions_parallel(
            self,
            prepared: List[Any],
            session_id: str,
            trace_id: Optional[str],
            iteration: int,
    ) -> List[Dict[str, Any]]:
        """Phase3: 并发执行已通过审批的多个独立工具 (有界 ≤4)。
        每个工具各自走 pre/post hook + 重试; hook 阻断/工具失败仅影响该工具,
        不拖累整批 (各返回各的结果, 顺序与 prepared 对齐)。"""
        from concurrent.futures import ThreadPoolExecutor

        def _one(item):
            tn, ti = item
            hook_ctx = {"trace_id": trace_id, "session_id": session_id,
                        "iteration": iteration, "phase": "react_parallel"}
            local_input = ti
            try:
                ni = self._hook_registry().fire("pre_tool_call", tn, local_input, hook_ctx)
                if isinstance(ni, dict):
                    local_input = ni
            except BlockedError as be:
                return {"tool_name": tn, "success": False, "code": be.code,
                        "output": {"error": be.message, "blocked_by_hook": True}}
            tr = self._call_tool_with_retry(
                step={"step_id": f"react_{iteration}_par_{tn}",
                      "tool_name": tn, "input_data": local_input},
                session_id=session_id, trace_id=trace_id, max_retries=self.max_retries,
            )
            try:
                nr = self._hook_registry().fire("post_tool_call", tn, local_input, tr, hook_ctx)
                if isinstance(nr, dict):
                    tr = nr
            except Exception:
                pass
            return tr

        if not prepared:
            return []
        with ThreadPoolExecutor(max_workers=min(4, len(prepared))) as ex:
            return list(ex.map(_one, prepared))

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
        parsed = None
        if match:
            try:
                parsed = json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                parsed = None
        if not isinstance(parsed, dict):
            # 严格 JSON 解析失败 (畸形 / 截断未闭合 / 无闭合括号) — qwen 给长答案时
            # react JSON 常畸形 (trailing comma / 未转义换行 / 被截断)。此前直接返 None →
            # 流式走 parse-fail 兜底把"原始 JSON 串"当答案 → 触发洗净重生成 (+20s)。
            # 这里尽量从畸形 JSON 里抠出干净 final_answer, 抠到就不必重生成 (治调试类延迟)。
            salvaged = ReActEngineMixin._salvage_final_answer(candidate)
            if salvaged is not None:
                return {"thought": "", "final_answer": salvaged}
            return None

        # 三选一: final_answer / actions(并行多动作) / action(单动作)
        if "final_answer" in parsed:
            return {"thought": parsed.get("thought", ""), "final_answer": parsed["final_answer"]}

        # Phase3: 多动作并行 — actions:[{tool,input}, ...] (一步并发多个相互独立的工具).
        # 只保留合法工具; 全非法 → None (fallback). 单个时退化等价单动作.
        avail = set(available_tools)
        raw_actions = parsed.get("actions")
        if isinstance(raw_actions, list) and raw_actions:
            valid = [
                {"tool": a.get("tool"), "input": a.get("input") or {}}
                for a in raw_actions
                if isinstance(a, dict) and a.get("tool") in avail
            ]
            if not valid:
                return None
            return {"thought": parsed.get("thought", ""), "actions": valid}

        action = parsed.get("action")
        if not isinstance(action, dict):
            return None
        tool_name = action.get("tool")
        if tool_name not in avail:
            return None
        return {
            "thought": parsed.get("thought", ""),
            "action": {
                "tool": tool_name,
                "input": action.get("input") or {},
            },
        }

    @staticmethod
    def _salvage_final_answer(raw: str) -> Optional[str]:
        """从畸形 JSON 里尽量抠出 final_answer 字符串值, 抠到返回纯文本否则 None.

        容错三类 qwen 长答案常见畸形: trailing comma / 未转义换行 / 被截断未闭合。
        策略: 定位 "final_answer": " 起点后逐字符扫到 *未转义* 闭合引号 (截断则到末尾),
        保留转义对交给 unescape。只针对 final_answer (有意给答案的场景), 抠不到不强解,
        不影响 action/actions 路径 (那些畸形仍按原逻辑 fallback)。best-effort, 不抛异常。
        """
        if not raw or not isinstance(raw, str):
            return None
        m = re.search(r'"final_answer"\s*:\s*"', raw)
        if not m:
            return None
        body = raw[m.end():]
        out: List[str] = []
        i, n = 0, len(body)
        while i < n:
            c = body[i]
            if c == "\\" and i + 1 < n:
                out.append(body[i:i + 2])  # 保留转义对 (\n \" \\ 等), 交给下面 unescape
                i += 2
                continue
            if c == '"':  # 未转义闭合引号 → 字符串体结束
                break
            out.append(c)
            i += 1
        captured = "".join(out)
        if not captured.strip():
            return None
        # 把抠出的 JSON 字符串体 unescape 成纯文本; 含字面换行 (控制符) 会让严格解析失败 →
        # 退回手动还原常见转义 (字面换行本身即真实内容, 原样保留)。
        try:
            return json.loads('"' + captured + '"')
        except (json.JSONDecodeError, ValueError):
            return (captured.replace('\\n', '\n').replace('\\t', '\t')
                    .replace('\\"', '"').replace('\\/', '/').replace('\\\\', '\\'))

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
