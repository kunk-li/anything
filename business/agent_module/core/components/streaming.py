# -*- coding: utf-8 -*-
"""
StreamingMixin (Task KK #71)

Agent 流式 generator. 任务期间 yield 每个 thought / action / observation / chunk / done.

依赖 SimpleAgent 字段:
    execute, logger, execution_strategy, max_react_iterations, max_retries,
    tool_approval_required, _resolve_llm_planner, _available_tool_names,
    _tool_descriptions, _build_react_prompt, _parse_react_response,
    _generate_plan, _needs_approval, _call_tool_with_retry, _summarize_tool_output
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


class StreamingMixin:
    """Agent 流式 generator (Task #48 + Task V/W 集成版)."""

    def run_stream(self, request: Dict[str, Any]):
        """Agent 流式 generator: yield ReAct 每一步 + final answer.

        Event 类型:
          {type: 'thought',     iteration, text}              LLM 思考
          {type: 'action',      iteration, tool_name, input}  即将执行的工具
          {type: 'observation', iteration, tool_name, success, output_summary}
          {type: 'chunk',       text}                          final answer 增量
          {type: 'meta',        steps, tool_results_summary}
          {type: 'plan',        plan, available_tools}         Task V plan_only
          {type: 'done',        cost_time, code}
          {type: 'error',       code, message}

        实现策略:
          - 仅 execution_strategy == 'react' 时走真实流; 'single_shot' 降级 execute + 一次性
          - 在 ReAct 循环里, 每个 thought/action/observation 立刻 yield 给 WS
          - final answer 切片 yield 给前端 (不调 chat_stream)
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
                yield {"type": "thought", "iteration": iteration, "text": thought}

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
            # Agent final answer 切片 (不调 chat_stream)
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
