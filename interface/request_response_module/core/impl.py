# -*- coding: utf-8 -*-
"""
请求响应处理模块具体实现类
实现完整请求响应处理全流程，串联协同调度模块与基础支撑层
"""

import time
import uuid
from typing import Dict, Any, Optional, Tuple

from .base import BaseRequestHandler
from ..model.data_model import UnifiedRequest, UnifiedResponse, ErrorDetails
from ..utils.tool_functions import validate_request_params, build_error_details

# 依赖模块导入（遵循设计文档依赖关系）
from orchestrator_module.core.impl import SimpleOrchestrator
from common_utils_module.core.impl import CommonUtils
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import ExceptionHandler


class RequestHandler(BaseRequestHandler):
    """标准请求响应处理实现类：参数校验 + 调度调用 + 响应封装，系统默认实现"""

    def __init__(self, orchestrator: SimpleOrchestrator):
        """
        初始化处理模块，注入协同调度实例，加载系统配置
        :param orchestrator: 协同调度模块实例（需实现 BaseOrchestrator 接口）
        """
        # 基础支撑层初始化
        self.utils = CommonUtils()
        self.logger = SystemLogger()
        self.config = ConfigManager()
        self.config.load_config()
        self.exception_handler = ExceptionHandler()

        # 核心依赖模块注入
        self.orchestrator = orchestrator

        # 读取系统处理核心配置
        self.default_type = self.config.get_config(
            "request_response.default_type",
            "rag"
        )
        self.enable_trace = self.config.get_config(
            "request_response.enable_trace",
            True
        )
        self.max_request_size = int(self.config.get_config(
            "request_response.max_request_size",
            1048576  # 1MB
        ))

        self.logger.info("请求响应处理模块初始化完成，加载系统默认配置")

    def validate_request(self, request: Dict[str, Any]) -> Tuple[bool, str]:
        """实现抽象方法：请求参数校验"""
        try:
            # 1. 检查请求大小
            request_size = len(str(request))
            if request_size > self.max_request_size:
                return False, f"请求大小超过限制（{self.max_request_size}字节）"

            # 2. 检查 type 是否合法
            req_type = request.get("type", self.default_type)
            if req_type not in ["rag", "agent", "hybrid"]:
                return False, f"不支持的请求类型：{req_type}，支持的类型为 rag/agent/hybrid"

            # 3. 检查 rag 模式下 query 是否存在
            if req_type == "rag" and not request.get("query"):
                return False, "RAG 模式必须提供 query 参数"

            # 4. 检查 agent/hybrid 模式下 task 是否存在
            if req_type in ["agent", "hybrid"] and not request.get("task"):
                return False, "Agent 模式必须提供 task 参数"

            # 5. 检查参数类型
            top_k = request.get("top_k", 5)
            if not isinstance(top_k, int) or top_k < 1 or top_k > 50:
                return False, "top_k 参数必须为 1-50 之间的整数"

            return True, ""

        except Exception as e:
            self.logger.error(f"请求校验异常：{str(e)}")
            return False, f"请求校验失败：{str(e)}"

    def handle(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """实现抽象方法：处理请求全流程"""
        start_time = time.time()
        trace_id = self._generate_trace_id()

        try:
            # 1. 参数校验
            is_valid, error_msg = self.validate_request(request)
            if not is_valid:
                self.logger.warning(f"请求校验失败：{error_msg}, trace_id={trace_id}")
                return self.format_response(
                    code="PARAM_MISSING",
                    message=error_msg,
                    data=None,
                    trace_id=trace_id,
                    cost_time=time.time() - start_time
                )

            # 2. 请求标准化
            standardized_request = self._standardize_request(request)

            # 3. 记录请求日志
            self.logger.info(f"处理请求：type={standardized_request.get('type')}, trace_id={trace_id}")

            # 4. 调用协同调度模块
            result = self.orchestrator.route(standardized_request)

            # 5. 封装响应
            response = self.format_response(
                code=result.get("code", "SUCCESS"),
                message=result.get("message", "ok"),
                data=result.get("data"),
                trace_id=trace_id,
                cost_time=time.time() - start_time
            )

            # 6. 记录响应日志
            self.logger.info(f"请求处理完成：code={response['code']}, trace_id={trace_id}")

            return response

        except Exception as e:
            self.logger.error(f"请求处理异常：{str(e)}, trace_id={trace_id}")
            return self.handle_exception(e, trace_id)

    def format_response(self, code: str, message: str, data: Any,
                        trace_id: str, cost_time: float = None) -> Dict[str, Any]:
        """实现抽象方法：格式化响应"""
        # 1. 判断是否成功
        is_success = code == "SUCCESS"

        # 2. 设置 retryable
        retryable = False
        if not is_success:
            # 根据错误码判断是否建议重试
            retryable_codes = [
                "VECTOR_QUERY_FAILED",
                "TOOL_CALL_FAILED",
                "AGENT_TIMEOUT",
                "API_RATE_LIMITED"
            ]
            retryable = code in retryable_codes

        # 3. 生成 details（失败时）
        details = None
        if not is_success:
            details = build_error_details(code, {})

        # 4. 构建统一响应结构
        response = {
            "code": code,
            "message": message,
            "data": data,
            "trace_id": trace_id,
            "retryable": retryable,
            "details": details
        }

        # 5. 添加耗时（若有）
        if cost_time is not None:
            response["cost_time"] = round(cost_time, 3)

        return response

    def handle_exception(self, exception: Exception, trace_id: str) -> Dict[str, Any]:
        """实现抽象方法：异常处理"""
        try:
            # 1. 调用异常处理模块获取标准化错误信息
            error_info = self.exception_handler.handle_exception(exception)

            # 2. 记录异常日志（包含 trace_id）
            self.logger.error(
                f"异常处理：code={error_info.get('code')}, "
                f"message={error_info.get('message')}, trace_id={trace_id}",
                exc_info=True
            )

            # 3. 构建错误响应（包含 details）
            response = {
                "code": error_info.get("code", "UNKNOWN_ERROR"),
                "message": error_info.get("message", "系统未知异常"),
                "data": None,
                "trace_id": trace_id,
                "retryable": False,
                "details": build_error_details(error_info.get("code", "UNKNOWN_ERROR"), {})
            }

            return response

        except Exception as e:
            # 异常处理本身失败，返回最简错误响应
            self.logger.critical(f"异常处理模块失败：{str(e)}")
            return {
                "code": "EXCEPTION_HANDLER_ERROR",
                "message": "异常处理器执行失败",
                "data": None,
                "trace_id": trace_id,
                "retryable": False,
                "details": None
            }

    def _generate_trace_id(self) -> str:
        """私有方法：生成链路追踪 ID"""
        if self.enable_trace:
            return uuid.uuid4().hex
        return "no_trace"

    def _standardize_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """私有方法：标准化请求格式"""
        standardized = request.copy()

        # 1. 设置默认 type（若未提供）
        if "type" not in standardized:
            standardized["type"] = self.default_type

        # 2. 设置默认 top_k（若未提供）
        if "top_k" not in standardized:
            standardized["top_k"] = 5

        # 3. 生成 session_id（若未提供）
        if "session_id" not in standardized or not standardized["session_id"]:
            standardized["session_id"] = f"session_{uuid.uuid4().hex[:12]}"

        # 4. 设置请求时间戳
        standardized["timestamp"] = self.utils.get_assist_tool().get_current_time()

        return standardized