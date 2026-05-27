# -*- coding: utf-8 -*-
"""
Agent 模块具体实现类
负责任务解析、工具调用、状态记录与结果聚合
"""

import json
import re
import time
import uuid
from typing import Dict, Any, List, Optional, Callable

from .base import BaseAgent

from deps_module import BasicDeps, build_basic_deps


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
        self.timeout = int(self.config.get_config("agent.timeout", timeout))
        self.max_retries = int(self.config.get_config("agent.max_retries", max_retries))
        self.session_prefix = self.config.get_config("agent.session_prefix", session_prefix)
        self.default_execution_mode = self.config.get_config("agent.default_execution_mode", "agent")
        # 是否启用 LLM 规划(默认 True; 失败时仍会 fallback 到规则式)
        self.use_llm_planner = bool(self.config.get_config("agent.use_llm_planner", True))
        # LLM 规划最多生成的 step 数,避免无限链路
        self.max_planner_steps = int(self.config.get_config("agent.max_planner_steps", 3))

        self.logger.info("Agent 模块初始化完成")

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

    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """执行 Agent 任务，统一接收标准 request dict"""
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

        try:
            self.logger.info(
                f"Agent执行开始：session_id={session_id}, trace_id={trace_id}, mode={execution_mode}"
            )

            self._append_state_event(
                session_id=session_id,
                event_type="task_started",
                trace_id=trace_id,
                payload={
                    "task": task,
                    "execution_mode": execution_mode,
                    "timeout": timeout,
                    "max_retries": max_retries,
                },
            )

            plan = self.parse_task(
                task=task,
                session_id=session_id,
                trace_id=trace_id,
                extra_params=extra_params,
            )

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

        # 3. 构造 prompt
        prompt = self._build_planner_prompt(task=task, available_tools=available_tools)

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

    @staticmethod
    def _build_planner_prompt(task: str, available_tools: List[str]) -> str:
        """构造 LLM 规划 prompt。"""
        tool_docs = {
            "rag_search": "在知识库中检索相关文档片段。input: {\"query\": str, \"top_k\": int}",
            "llm_generate": "调用大语言模型生成文本。input: {\"prompt\": str}",
        }
        tool_lines = []
        for name in available_tools:
            doc = tool_docs.get(name, "(无描述)")
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
        """按重试策略调用工具"""
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

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                output = tool(payload)

                is_success = True
                if isinstance(output, dict):
                    code = output.get("code")
                    if code and code != "SUCCESS":
                        is_success = False

                return {
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
        """将工具输出压缩为简短摘要"""
        if isinstance(output, dict):
            if "data" in output and isinstance(output["data"], dict):
                data = output["data"]
                if "answer" in data:
                    return str(data["answer"])[:80]
                if "text" in data:
                    return str(data["text"])[:80]
                if "content" in data:
                    return str(data["content"])[:80]
            if "answer" in output:
                return str(output["answer"])[:80]
            if "text" in output:
                return str(output["text"])[:80]
            if "content" in output:
                return str(output["content"])[:80]
            return str(output)[:80]
        return str(output)[:80]

    def _handle_exception(
            self,
            exception: Exception,
            trace_id: Optional[str],
            session_id: Optional[str],
            task: Optional[str],
    ) -> Dict[str, Any]:
        """统一异常处理"""
        try:
            if hasattr(self.exception_handler, "handle"):
                error_info = self.exception_handler.handle(exception, trace_id=trace_id)
            else:
                error_info = self.exception_handler.handle_exception(exception)

            return {
                "code": error_info.get("code", "AGENT_RUN_FAILED"),
                "message": error_info.get("message", "Agent执行失败"),
                "data": None,
                "trace_id": trace_id,
                "retryable": error_info.get("retryable", False),
                "details": error_info.get("details") or {
                    "session_id": session_id,
                    "task": task,
                    "stage": "agent",
                },
            }
        except Exception:
            return {
                "code": "AGENT_RUN_FAILED",
                "message": "Agent执行失败",
                "data": None,
                "trace_id": trace_id,
                "retryable": False,
                "details": {
                    "session_id": session_id,
                    "task": task,
                    "stage": "agent",
                },
            }

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
