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

    def _persist_stream_answer(self, session_id, task, answer, trace_id, status="completed"):
        """ZZ-4: 持久化流式答案到 state_store (后端单一数据源).

        success 路径 status=completed; 用户停止 / 中途异常时 status=interrupted,
        已生成的半截答案也存下来 (前端切会话 / 刷新仍能看到, 不丢). answer 空则跳过.
        """
        if not answer:
            return
        try:
            self._save_state_safe(
                session_id=session_id,
                state={
                    "status": status,
                    "task": task,
                    "answer": answer,
                    "execution_mode": "agent",
                    "updated_at": time.time(),
                },
                trace_id=trace_id,
            )
        except Exception as _se:
            self.logger.warning(f"[agent-stream] 持久化失败(忽略): {_se}")

    def _run_stream_direct(self, task, trace_id, request, extra_params, start_time):
        """默认真 token 流式: 注入 memory + system_prompt, 调 chat_stream 逐 token yield.

        作为 generator 用 `yield from` 调用; return True 表示成功流完 (调用方 return),
        return False 表示 chat_stream 不可用/失败 (调用方降级到 ReAct/execute).
        """
        # 1. 注入长期记忆 (跟 execute 一致, 让多轮对话有上下文)
        augmented = task
        try:
            if getattr(self, "memory_enabled", False):
                augmented, _hits = self._inject_long_term_memory(
                    task, self._memory_tenant(request), trace_id,
                )
        except Exception:
            augmented = task
        # 2. 注入对话历史 (多轮上下文) — 修金鱼记忆. 读 state_store 最近 N 轮.
        hist_block = ""
        try:
            history = self._load_history(request.get("session_id"))
            if history:
                lines = []
                for h in history:
                    who = "用户" if h.get("role") == "user" else "助手"
                    lines.append(f"{who}: {h.get('content', '')}")
                hist_block = "[对话历史]\n" + "\n".join(lines) + "\n\n"
        except Exception:
            hist_block = ""
        # 3. per-session system prompt (PM-7-3)
        sys_prompt = (extra_params.get("system_prompt") or "").strip()
        sys_block = f"[系统指令]\n{sys_prompt}\n\n" if sys_prompt else ""
        prompt = f"{sys_block}{hist_block}[当前问题]\n{augmented}" if (sys_block or hist_block) else augmented
        # 4. 先发空 meta (前端清 trace 区), 再逐 token
        yield {"type": "meta", "steps": [], "tool_results_summary": [],
               "citations": [], "retrieved_chunks": []}
        got_any = False
        buf = []
        sid = request.get("session_id")
        try:
            for token in self.llm_client.chat_stream(prompt=prompt, trace_id=trace_id):
                if token:
                    got_any = True
                    buf.append(str(token))
                    yield {"type": "chunk", "text": str(token)}
        except GeneratorExit:
            # ZZ-4: 用户点停止 → WS 断开 → 上层 gen.close() 在此 yield 处抛 GeneratorExit.
            # 把已经流给用户的半截答案落库 (后端单一数据源, 切会话/刷新不丢), 再重新抛出.
            # 注意: GeneratorExit 处理块内不能再 yield, 只能做无 yield 的 IO 后 raise.
            if got_any:
                self._persist_stream_answer(sid, task, "".join(buf), trace_id,
                                            status="interrupted")
            raise
        except Exception as e:
            # ZZ-4: chat_stream 中途异常. 若已吐出部分 token, 存半截 + 报错, 不再降级重跑
            # (否则用户先看到半截答案, 又冒出第二个完整答案, 很迷惑). 一个 token 都没产出
            # 才返回 False 让上层降级 (ReAct / execute) 真正重试.
            self.logger.warning(f"[agent-stream] chat_stream 异常: {e}")
            if got_any:
                self._persist_stream_answer(sid, task, "".join(buf), trace_id,
                                            status="interrupted")
                yield {"type": "error", "code": "STREAM_INTERRUPTED",
                       "message": f"生成中断: {e}"}
                return True
            return False
        if not got_any:
            return False
        # 关键 (后端单一数据源): 流式对话完成后, 持久化 user task + assistant answer
        # 到 state_store. _save_state_safe 会读老 events 并 append 这两条 (merge,
        # 不覆盖历史). 切会话/刷新页面时前端从后端拉, 不再丢失流式对话.
        self._persist_stream_answer(sid, task, "".join(buf), trace_id)
        yield {"type": "done", "code": "SUCCESS",
               "cost_time": round(time.time() - start_time, 3)}
        return True

    def run_stream(self, request: Dict[str, Any]):
        """Agent 流式入口: 包一层模型分级路由 (执行计划③) 后委托 _run_stream_impl。
        generator: set 路由 (按任务复杂度) → yield from 实现 → finally 重置 (耗尽/关闭都重置)。
        默认关 → begin_routing 返 None → 零行为变化。"""
        from ..model_routing import begin_routing, end_routing
        _tok = begin_routing(self, (request or {}).get("task"))
        try:
            yield from self._run_stream_impl(request)
        finally:
            end_routing(_tok)

    def _run_stream_impl(self, request: Dict[str, Any]):
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

        # ============ 默认: 真 token 流式 (直连 llm_client.chat_stream) ============
        # 覆盖对话/写作/问答主场景 — TTFT 快, 逐字吐. 跟 RAG 真流式同一套 chat_stream.
        # 工具调用场景 (plan_only / 显式 execution_strategy=react) 跳过此分支, 走下面
        # ReAct 多轮 (能看到 thought/action/observation + 工具结果).
        # 修"流式 agent 不调工具(含 system_info)、只会说做不了": 是否走 ReAct(带工具) 此前
        # 只看单次请求的 extra_params.execution_strategy, 忽略了 self.execution_strategy
        # (config 默认 react)。前端 type=agent 不传 extra_params.execution_strategy → 永远
        # 落到 _run_stream_direct 纯 chat 分支 → 永不调工具。这里把 self.execution_strategy
        # 也算进 effective strategy, 与非流式 execute() 对齐。
        _effective_strategy = (extra_params.get("execution_strategy")
                               or getattr(self, "execution_strategy", "single_shot"))
        # 有附件强制 ReAct (与非流式 execute 对齐): 附件须经工具读取, 直连 chat_stream
        # 纯文本不跑工具, 模型根本看不到附件。
        _want_react = (
            _effective_strategy == "react"
            or (bool(extra_params.get("plan_only")) and not bool(extra_params.get("approve_plan")))
            or bool(extra_params.get("attachments"))
        )
        if (not _want_react and getattr(self, "llm_client", None) is not None
                and hasattr(self.llm_client, "chat_stream")):
            _ok = yield from self._run_stream_direct(
                task=task, trace_id=trace_id, request=request,
                extra_params=extra_params, start_time=start_time,
            )
            if _ok:
                return
            # chat_stream 失败 → 落到下面原逻辑兜底

        # ZZ-4: ReAct 分支以前只看 self.execution_strategy (Agent 全局默认, 通常 single_shot),
        # 忽略了刚算出的 _want_react → 导致 extra_params.execution_strategy=react / plan_only
        # 被悄悄丢弃, 走 single_shot execute() (工具能跑但没有 thought/action/observation 实时
        # 轨迹). 图片附件场景前端就靠这个 flag 路由到 ReAct, 所以这里要把 _want_react 也算上.
        if not _want_react and self.execution_strategy != "react":
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

        # ZZ-5: 多轮上下文 — 流式 ReAct / plan 也注入对话历史 (默认流式 _run_stream_direct 已有,
        # 这里补上工具/计划场景, 否则 ReAct 仍是金鱼记忆).
        if task:
            _hist_prefix = self._history_prefix(session_id)
            if _hist_prefix:
                task = _hist_prefix + task

        # 附件块注入 (与非流式 _pre_step_attachments 对齐 — 流式 react 不走前处理流水线)
        _att_suffix = self._attachments_task_suffix(extra_params)
        if _att_suffix:
            task = (task or "") + _att_suffix

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
        # AUDIT-2a: 流式 ReAct wall-clock 超时 (per-request timeout 覆盖 self.timeout)
        _stream_timeout = int(request.get("timeout") or getattr(self, "timeout", 0) or 0)

        try:
            for iteration in range(1, self.max_react_iterations + 1):
                # AUDIT-2a: 每轮入口检查超时; 超过则停止开新轮 — 但不能像旧版那样直接
                # yield done(AGENT_TIMEOUT) 丢弃已收集的全部工具结果 (前端收到 0 chunk
                # 渲染成空白气泡)。break 进下方收尾流程: 基于已有结果组答案+meta+chunks。
                if _stream_timeout and (time.time() - start_time) > _stream_timeout:
                    self.logger.warning(
                        f"[react-stream] wall-clock 超时 ({_stream_timeout}s, 已完成 "
                        f"{len(history)} 轮), 停止迭代基于已收集结果收尾: trace_id={trace_id}"
                    )
                    yield {"type": "loop_break", "iteration": iteration,
                           "message": (f"已达 {_stream_timeout}s 时间上限, 停止继续调用工具, "
                                       f"基于已收集的结果作答。")}
                    break
                prompt = self._build_react_prompt(
                    task=task, available_tools=available_tools,
                    history=history, iteration=iteration,
                    max_iterations=self.max_react_iterations,
                    tool_descriptions=tool_descriptions,
                )
                # 健壮: react 计划 LLM 调用撞网络抖动会抛异常 — 重试 (短退避) 而非直接报错。
                # 本 generator 在线程池跑 (handle_stream via run_in_executor), time.sleep 安全。
                raw = None
                _last_err = None
                for _attempt in range(3):
                    try:
                        raw = llm_call(prompt)
                        _last_err = None
                        break
                    except Exception as e:
                        _last_err = e
                        self.logger.warning(
                            f"[react-stream] LLM 调用异常 iter={iteration} 第{_attempt + 1}/3 次: {e}"
                        )
                        if _attempt < 2:
                            time.sleep(0.8 * (_attempt + 1))  # 0.8s → 1.6s 退避
                if _last_err is not None:
                    yield {"type": "error", "code": "AGENT_RUN_FAILED",
                           "message": f"LLM 调用异常 iter={iteration} (重试 3 次仍失败): {_last_err}"}
                    return

                step = self._parse_react_response(raw, available_tools)
                if step is None:
                    # 健壮性: LLM 没按 ReAct 的 JSON 格式输出 — 规划/建议/写作类任务里 qwen 等常
                    # 直接给自然语言答案 (不套 JSON 壳)。与其报 AGENT_RUN_FAILED 让用户白等,
                    # 不如把这段自然语言当最终答案 (LLM 显然在回答, 只是没套壳), 走收尾流程。
                    raw_text = (raw or "").strip()
                    if raw_text:
                        final_answer = raw_text
                        history.append({"thought": "", "final_answer": final_answer})
                        break
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

                # Phase3 修: 多动作并行 — 与非流式 _react_execute 对齐。此前流式漏处理 actions:[],
                # 导致 LLM 并行多个独立工具时 tool_name=None 空转、工具不执行 (实测暴露)。审批整批门控。
                multi_actions = step.get("actions")
                if multi_actions:
                    prepared = []
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
                        yield {"type": "action", "iteration": iteration, "tool_name": tn,
                               "input": {k: v for k, v in ti.items()
                                         if k not in ("trace_id", "session_id", "extra_params")}}
                    if blocked_tool is not None:
                        yield {"type": "error", "code": "TOOL_APPROVAL_REQUIRED",
                               "message": f"工具 '{blocked_tool}' 需要审批; 请带 extra_params.approve_tools=['{blocked_tool}'] 重提.",
                               "tool_name": blocked_tool,
                               "required_tools": sorted(self.tool_approval_required)}
                        return
                    par_results = self._run_actions_parallel(prepared, session_id, trace_id, iteration)
                    for (tn, ti), tr in zip(prepared, par_results):
                        tool_results.append(tr)
                        obs = self._summarize_tool_output(tr.get("output"))
                        last_observation = tr
                        history.append({"thought": thought, "action": {"tool": tn, "input": ti},
                                        "observation": obs})
                        yield {"type": "observation", "iteration": iteration, "tool_name": tn,
                               "success": tr.get("success", False), "output_summary": obs}
                    continue

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

                # 空转硬兜底 (与非流式 _react_execute 对齐): 同一 (工具+入参) 产出相同观察
                # 累计 ≥3 次 → 判 ReAct 空转, 提前收尾; 下面"循环结束"会用 LLM 把已查到的
                # 结果整合成自然语言答案 (而不是继续空转把迭代打满)。
                if self._is_spinning(history, tool_name, tool_input, observation):
                    self.logger.warning(
                        f"[react-stream] 检测到空转 (同命令重复且结果一致 ≥3 次), 提前结束: "
                        f"tool={tool_name} iter={iteration}"
                    )
                    yield {"type": "loop_break", "iteration": iteration, "tool_name": tool_name,
                           "message": "检测到重复执行同一命令且结果一致, 已停止重试, 基于已有结果作答。"}
                    break

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
            elif not self._sanitize_final_answer(final_answer):
                # ZZ-6 (#122): 裸工具名 (LLM 幻觉) → 兜底
                final_answer = "抱歉, 这次没能正确组织出答案, 换个说法再试一次。"

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
                     "summary": self._summarize_tool_output(tr.get("output")),
                     # Task XXXX-11 (#158): 结构化 images 字段, 前端直接读
                     "images": self._extract_image_urls(tr.get("output")) if hasattr(self, "_extract_image_urls") else []}
                    for tr in tool_results
                ],
                "citations": [], "retrieved_chunks": [],
            }
            # Agent final answer: 优先调 llm_service.chat_stream 真 token 流;
            # llm_service 没暴露 chat_stream 时降级为切片伪流.
            # 注: 零工具调用场景下 (LLM 直接给 final_answer), 用 task 重新流式生成
            # 答案; 有工具调用时, 用 task + 工具结果 prompt 重新流式总结.
            # 最终答案: 健壮性优先 (用户多次遇"显示不全/截断/空白")。早先用 chat_stream SSE 真
            # token 流, 但 SSE 中途断会留半截/空白答案 (实测同一问题 3 次得 len=0 / 截断 / 完整,
            # 极不稳)。改为: 用非流式 llm_call 一次性拿完整答案 (单次响应, 抗中途断 + 可重试),
            # 再切片伪流给前端保留打字机观感; 生成失败/空则退回 react final_answer。保证答案完整。
            _streamed_text = []
            answer_out = final_answer or ""
            # 治延迟 (#2): 默认"仅有工具结果时才重生成"(把工具输出总结成自然语言 — react final_answer
            # 未必完整带上工具输出); 无工具的纯回答/规划/建议直接用 react 的 final_answer (已在完整
            # 上下文含命中技能/历史下生成), 省一次 LLM 调用、不丢上下文。
            # 例外 (健壮性, 勿删): react 输出解析失败时 final_answer 会退化成"原始 JSON 串"
            # (react_engine parse-fail 兜底), 绝不能直接喷给用户 → 即便无工具也重生成一次干净作答。
            # 这是无工具重生成原本承担的"洗净"职责, 治延迟时不能连它一起删 (否则解析失败就喷生 JSON)。
            _fa = str(final_answer or "").strip()
            _is_raw_react_json = _fa.startswith("{") and (
                '"thought"' in _fa or '"action"' in _fa or '"final_answer"' in _fa)
            _auth = self._authoritative_answer(tool_results)
            if _auth:
                # 工具权威完整结果 (如 software_info list): 直接用, 不再调 LLM 重生成
                # (确定性、完整, 避免 max_tokens 截断 / 漏项)。
                answer_out = _auth
            elif tool_results or _is_raw_react_json:
                try:
                    if tool_results:
                        tool_ctx_parts = []
                        for tr in tool_results:
                            tn = tr.get("tool_name", "?")
                            summary = self._summarize_tool_output(tr.get("output"))
                            tool_ctx_parts.append(f"[工具 {tn} 结果]\n{summary}")
                        tool_ctx = "\n\n".join(tool_ctx_parts)
                        final_prompt = (
                            f"用户任务: {task}\n\n已收集到的信息:\n{tool_ctx}\n\n"
                            f"请基于以上信息, 用自然语言完整地直接回答用户 (一次写完整, 不要中途停下)."
                        )
                    else:
                        # 无工具但解析失败: 用 task 重新干净作答 (恢复"洗净", 生 JSON 不外泄)
                        final_prompt = str(task or "")
                    _full = llm_call(final_prompt)
                    if _full and str(_full).strip():
                        answer_out = str(_full)
                except Exception as e:
                    self.logger.warning(f"[react-stream] 最终答案重生成失败, 用 react final_answer: {e}")
            # 铁底兜底: 绝不让最终答案为 (a) 空白 或 (b) 解析失败残留的"原始 JSON 串"喷给用户
            # (重生成也失败/异常时的最后一道防线 — 用户曾遇主答案区空白)。两种都退到干净提示语。
            _ao = str(answer_out or "").strip()
            _bad = (not _ao) or (_ao.startswith("{") and (
                '"thought"' in _ao or '"final_answer"' in _ao or '"action"' in _ao))
            if _bad:
                answer_out = "抱歉, 这次没能正确组织出答案, 请重试或换个说法描述你的需求。"
            _streamed_text = [answer_out]
            chunk_size = max(1, len(answer_out) // 60)
            for i in range(0, len(answer_out), chunk_size):
                yield {"type": "chunk", "text": answer_out[i:i + chunk_size]}

            # 持久化到后端 state_store (单一数据源) — 跟 _run_stream_direct 一致.
            # ReAct 工具场景的对话也存, 切会话/刷新不丢.
            try:
                self._save_state_safe(
                    session_id=session_id,
                    state={
                        "status": "completed",
                        # 存原始用户输入: ReAct 路径上面把 task 拼了 _history_prefix 喂 LLM 当上下文,
                        # 不能把含历史的 task 存进会话 (否则前端把"用户消息"回显成一大段对话历史)。
                        "task": request.get("task") or task,
                        "answer": "".join(_streamed_text) or final_answer,
                        "execution_mode": "agent",
                        "updated_at": time.time(),
                    },
                    trace_id=trace_id,
                )
            except Exception as _se:
                self.logger.warning(f"[react-stream] 持久化失败(忽略): {_se}")

            # #1 技能自动沉淀: 成功且复杂的任务 → 后台线程提炼可复用 skill (默认关, 不阻塞 done)。
            try:
                self._distill_skill_async(
                    task=request.get("task"),  # 原始 task (未拼历史前缀), 提炼更通用
                    tool_results=tool_results,
                    final_answer="".join(_streamed_text) or final_answer,
                    trace_id=trace_id,
                )
            except Exception:
                pass

            yield {
                "type": "done",
                "code": "SUCCESS",
                "cost_time": round(time.time() - start_time, 3),
                "iterations_used": len(history),
            }
        except Exception as e:
            self.logger.error(f"[react-stream] 异常: {e}, trace_id={trace_id}")
            yield {"type": "error", "code": "UNKNOWN_ERROR", "message": str(e)}
