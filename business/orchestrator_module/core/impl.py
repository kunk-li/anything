# -*- coding: utf-8 -*-
"""
协同调度模块具体实现类
负责在 rag / agent / hybrid 三种模式间做统一路由与最小结果封装
"""

import time
from typing import Dict, Any, Optional

from .base import BaseOrchestrator

from deps_module import BasicDeps, build_basic_deps, handle_exception_to_envelope


class SimpleOrchestrator(BaseOrchestrator):
    """标准协同调度实现：根据 type 分发到 RAG / Agent / Hybrid"""

    def __init__(self, rag_runner=None, agent_runner=None, deps: Optional[BasicDeps] = None):
        # 基础依赖优先走 DI 注入；未注入时构造一套（向后兼容）
        deps = deps or build_basic_deps()
        self.utils = deps.utils
        self.logger = deps.logger
        self.config = deps.config
        self.exception_handler = deps.exception_handler

        self.rag_runner = rag_runner
        self.agent_runner = agent_runner

        self.default_type = self.config.get_config("orchestrator.default_type", "rag")
        self.enable_trace = self.config.get_config("orchestrator.enable_trace", True)
        self.hybrid_strategy = self.config.get_config("orchestrator.hybrid_strategy", "agent_driven")

        self.logger.info("协同调度模块初始化完成")

    def register_modules(self, rag_runner=None, agent_runner=None) -> None:
        """注册或替换下游执行器"""
        if rag_runner is not None:
            self.rag_runner = rag_runner
        if agent_runner is not None:
            self.agent_runner = agent_runner

    def route(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """根据请求类型路由并执行对应业务链路"""
        start_time = time.time()
        trace_id = request.get("trace_id")
        req_type = request.get("type") or self.default_type

        try:
            self.logger.info(
                f"协同调度开始：type={req_type}, trace_id={trace_id}, session_id={request.get('session_id')}"
            )

            if req_type == "rag":
                result = self._execute_rag(request)
            elif req_type == "agent":
                result = self._execute_agent(request)
            elif req_type == "hybrid":
                result = self._execute_hybrid(request)
            else:
                return {
                    "code": "BAD_REQUEST",
                    "message": f"不支持的type：{req_type}",
                    "data": None,
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": {
                        "field": "type",
                        "allowed": ["rag", "agent", "hybrid"],
                        "actual": req_type,
                    },
                    "cost_time": round(time.time() - start_time, 3),
                }

            # 下游结果最小补齐，不再额外套娃包装
            response = {
                "code": result.get("code", "SUCCESS"),
                "message": result.get("message", "ok"),
                "data": result.get("data"),
                "trace_id": result.get("trace_id", trace_id),
                "retryable": result.get("retryable", False),
                "details": result.get("details"),
                "cost_time": round(time.time() - start_time, 3),
            }

            self.logger.info(
                f"协同调度完成：type={req_type}, code={response['code']}, trace_id={response['trace_id']}"
            )
            return response

        except Exception as e:
            self.logger.error(f"协同调度异常：{str(e)}, trace_id={trace_id}")
            return self._handle_exception(e, trace_id=trace_id, request=request)

    def route_stream(self, request: Dict[str, Any]):
        """流式路由 generator (Task #44).

        仅当 type=rag 且 rag_runner 支持 run_stream 时, yield 真实 token 流;
        其他类型 (agent/hybrid) yield NotImplemented 让 handler 降级到 route()。

        Event 形态:
          {type: 'meta'|'chunk'|'done'|'error', ...}
        """
        request_type = (request.get("type") or "rag").lower()
        if request_type == "rag":
            if self.rag_runner is None or not hasattr(self.rag_runner, "run_stream"):
                yield {"type": "error", "code": "RAG_RUN_FAILED",
                       "message": "rag_runner 未支持流式"}
                return
            yield from self.rag_runner.run_stream(dict(request))
            return
        # 其他类型不支持流式
        yield {"type": "error", "code": "BAD_REQUEST",
               "message": f"流式当前仅支持 type=rag, 收到: {request_type}"}

    def _execute_rag(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """执行 RAG 请求"""
        if self.rag_runner is None:
            return {
                "code": "RAG_RUN_FAILED",
                "message": "RAG执行器未注册",
                "data": None,
                "trace_id": request.get("trace_id"),
                "retryable": False,
                "details": {"component": "rag_runner"},
            }

        # 统一使用标准 request dict 调下游
        return self.rag_runner.run(dict(request))

    def _execute_agent(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """执行 Agent 请求"""
        if self.agent_runner is None:
            return {
                "code": "AGENT_RUN_FAILED",
                "message": "Agent执行器未注册",
                "data": None,
                "trace_id": request.get("trace_id"),
                "retryable": False,
                "details": {"component": "agent_runner"},
            }

        return self.agent_runner.execute(dict(request))

    def _execute_hybrid(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """执行 Hybrid 请求：当前版本定义为 Agent 主导 + RAG 工具协作"""
        if self.agent_runner is None:
            return {
                "code": "AGENT_RUN_FAILED",
                "message": "Hybrid模式所需的Agent执行器未注册",
                "data": None,
                "trace_id": request.get("trace_id"),
                "retryable": False,
                "details": {"component": "agent_runner", "mode": "hybrid"},
            }

        hybrid_request = dict(request)
        hybrid_request.setdefault("extra_params", {})
        hybrid_request["extra_params"]["execution_mode"] = "hybrid"
        hybrid_request["extra_params"]["hybrid_strategy"] = self.hybrid_strategy

        return self.agent_runner.execute(hybrid_request)

    def _handle_exception(
        self,
        exception: Exception,
        trace_id: Optional[str] = None,
        request: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """统一异常处理(委托给 deps_module.handle_exception_to_envelope)。"""
        return handle_exception_to_envelope(
            exception_handler=self.exception_handler,
            exception=exception,
            trace_id=trace_id,
            fallback_code="ORCHESTRATOR_RUN_FAILED",
            fallback_message="协同调度执行失败",
            stage="orchestrator",
            context={"type": (request or {}).get("type")},
        )

    def register_module(self, module_type: str, module_instance: Any) -> None:
        """
        兼容旧抽象接口：注册单个下游模块
        module_type: rag / agent
        """
        if module_type == "rag":
            self.rag_runner = module_instance
            return
        if module_type == "agent":
            self.agent_runner = module_instance
            return
        raise ValueError(f"不支持的模块类型：{module_type}")

    def call_orchestrator(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        兼容旧抽象接口：转调新的 route()
        """
        return self.route(request)