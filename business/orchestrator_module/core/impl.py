# -*- coding: utf-8 -*-
"""
协同调度模块具体实现类
负责在 rag / agent / hybrid 三种模式间做统一路由与最小结果封装
"""

import time
from typing import Dict, Any, Optional

from .base import BaseOrchestrator

from deps_module import BasicDeps, build_basic_deps


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
        """统一异常处理"""
        try:
            if hasattr(self.exception_handler, "handle"):
                error_info = self.exception_handler.handle(exception, trace_id=trace_id)
            else:
                error_info = self.exception_handler.handle_exception(exception)

            return {
                "code": error_info.get("code", "ORCHESTRATOR_RUN_FAILED"),
                "message": error_info.get("message", "协同调度执行失败"),
                "data": None,
                "trace_id": trace_id,
                "retryable": error_info.get("retryable", False),
                "details": error_info.get("details") or {
                    "stage": "orchestrator",
                    "type": (request or {}).get("type"),
                },
            }
        except Exception:
            return {
                "code": "ORCHESTRATOR_RUN_FAILED",
                "message": "协同调度执行失败",
                "data": None,
                "trace_id": trace_id,
                "retryable": False,
                "details": {"stage": "orchestrator"},
            }

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