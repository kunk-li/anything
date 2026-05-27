# -*- coding: utf-8 -*-
"""
请求响应处理模块具体实现类
实现完整请求响应处理全流程，串联协同调度模块与基础支撑层
"""

import time
import uuid
from typing import Dict, Any, Optional, Tuple

from .base import BaseRequestHandler
from ..utils.tool_functions import validate_request_params, build_error_details

from deps_module import BasicDeps, build_basic_deps


class RequestHandler(BaseRequestHandler):
    """标准请求响应处理实现类：参数校验 + 调度调用 + 响应封装，系统默认实现"""

    def __init__(self, orchestrator, deps: Optional[BasicDeps] = None):
        """
        初始化处理模块，注入协同调度实例，加载系统配置
        :param orchestrator: 协同调度模块实例（建议实现 BaseOrchestrator 接口）
        :param deps: 基础依赖容器；未提供时自行构造（向后兼容）
        """
        # 基础依赖优先走 DI 注入
        deps = deps or build_basic_deps()
        self.utils = deps.utils
        self.logger = deps.logger
        self.config = deps.config
        self.exception_handler = deps.exception_handler

        self.orchestrator = orchestrator

        self.default_type = self.config.get_config("request_response.default_type", "rag")
        self.enable_trace = self.config.get_config("request_response.enable_trace", True)
        self.max_request_size = int(
            self.config.get_config("request_response.max_request_size", 1048576)
        )

        self.logger.info("请求响应处理模块初始化完成，加载系统默认配置")

    def validate_request(self, request: Dict[str, Any]) -> Tuple[bool, str, str]:
        """请求参数校验，返回 (是否通过, message, code)"""
        try:
            request_size = len(str(request))
            if request_size > self.max_request_size:
                return False, f"请求大小超过限制（{self.max_request_size}字节）", "REQUEST_TOO_LARGE"

            result = validate_request_params(request)

            if isinstance(result, tuple):
                if len(result) == 3:
                    is_valid, message, code = result
                    return bool(is_valid), str(message), str(code)
                if len(result) == 2:
                    is_valid, message = result
                    if is_valid:
                        return True, "", "SUCCESS"
                    msg = str(message)
                    if "type" in msg or "请求类型" in msg or "rag/agent/hybrid" in msg:
                        return False, msg, "BAD_REQUEST"
                    if "top_k" in msg or "参数类型" in msg:
                        return False, msg, "PARAM_INVALID"
                    return False, msg, "PARAM_MISSING"

            return False, "请求校验返回格式非法", "UNKNOWN_ERROR"

        except Exception as e:
            self.logger.error(f"请求校验异常：{str(e)}")
            return False, f"请求校验失败：{str(e)}", "UNKNOWN_ERROR"

    def handle(self, request: Dict[str, Any], trace_id: Optional[str] = None) -> Dict[str, Any]:
        """处理请求全流程"""
        start_time = time.time()
        trace_id = trace_id or request.get("trace_id") or self._generate_trace_id()

        try:
            is_valid, error_msg, error_code = self.validate_request(request)
            if not is_valid:
                self.logger.warning(f"请求校验失败：{error_msg}, trace_id={trace_id}")
                return self.format_response(
                    code=error_code,
                    message=error_msg,
                    data=None,
                    trace_id=trace_id,
                    request_context=request,
                    cost_time=time.time() - start_time,
                )

            standardized_request = self._standardize_request(request, trace_id=trace_id)

            self.logger.info(
                f"处理请求：type={standardized_request.get('type')}, "
                f"trace_id={trace_id}, session_id={standardized_request.get('session_id')}"
            )

            result = self.orchestrator.route(standardized_request)

            response = self.format_response(
                code=result.get("code", "SUCCESS"),
                message=result.get("message", "ok"),
                data=result.get("data"),
                trace_id=result.get("trace_id", trace_id),
                request_context=standardized_request,
                cost_time=time.time() - start_time,
            )

            if "retryable" in result:
                response["retryable"] = result["retryable"]
            if "details" in result and result["details"] is not None:
                response["details"] = result["details"]

            self.logger.info(f"请求处理完成：code={response['code']}, trace_id={trace_id}")
            return response

        except Exception as e:
            self.logger.error(f"请求处理异常：{str(e)}, trace_id={trace_id}")
            return self.handle_exception(e, trace_id, request_context=request)

    def format_response(
        self,
        code: str,
        message: str,
        data: Any,
        trace_id: str,
        request_context: Optional[Dict[str, Any]] = None,
        cost_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        """格式化响应"""
        is_success = code == "SUCCESS"

        retryable = False
        if not is_success:
            retryable_codes = {
                "VECTOR_QUERY_FAILED",
                "TOOL_CALL_FAILED",
                "AGENT_TIMEOUT",
                "API_RATE_LIMITED",
                "RAG_RUN_FAILED",
                "ORCHESTRATOR_RUN_FAILED",
            }
            retryable = code in retryable_codes

        details = None
        if not is_success:
            try:
                details = build_error_details(code, request_context or {}, message)
            except TypeError:
                details = build_error_details(code, request_context or {})

        response = {
            "code": code,
            "message": message,
            "data": data,
            "trace_id": trace_id,
            "retryable": retryable,
            "details": details,
        }

        if cost_time is not None:
            response["cost_time"] = round(cost_time, 3)

        return response

    def handle_exception(
        self,
        exception: Exception,
        trace_id: str,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """异常处理"""
        try:
            if hasattr(self.exception_handler, "handle"):
                error_info = self.exception_handler.handle(exception, trace_id=trace_id)
            else:
                error_info = self.exception_handler.handle_exception(exception)

            error_code = error_info.get("code", "UNKNOWN_ERROR")
            error_message = error_info.get("message", "系统未知异常")
            retryable = bool(error_info.get("retryable", False))

            self.logger.error(
                f"异常处理：code={error_code}, message={error_message}, trace_id={trace_id}",
                exc_info=True,
            )

            details = error_info.get("details")
            if details is None:
                try:
                    details = build_error_details(error_code, request_context or {}, error_message)
                except TypeError:
                    details = build_error_details(error_code, request_context or {})

            return {
                "code": error_code,
                "message": error_message,
                "data": None,
                "trace_id": trace_id,
                "retryable": retryable,
                "details": details,
            }

        except Exception as e:
            self.logger.critical(f"异常处理模块失败：{str(e)}")
            return {
                "code": "EXCEPTION_HANDLER_ERROR",
                "message": "异常处理器执行失败",
                "data": None,
                "trace_id": trace_id,
                "retryable": False,
                "details": None,
            }

    def _generate_trace_id(self) -> str:
        """生成链路追踪 ID（仅接口层兜底）"""
        if self.enable_trace:
            return uuid.uuid4().hex
        return "no_trace"

    def _standardize_request(self, request: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
        """标准化请求格式"""
        standardized = dict(request)

        if "type" not in standardized or not standardized.get("type"):
            standardized["type"] = self.default_type

        if "top_k" not in standardized or standardized.get("top_k") is None:
            standardized["top_k"] = 5

        if "extra_params" not in standardized or standardized["extra_params"] is None:
            standardized["extra_params"] = {}

        standardized["trace_id"] = trace_id

        req_type = standardized.get("type")
        if req_type in ["agent", "hybrid"] and not standardized.get("session_id"):
            standardized["session_id"] = f"session_{uuid.uuid4().hex[:12]}"

        return standardized
