# -*- coding: utf-8 -*-
"""
协同调度模块具体实现类
实现完整调度全流程，串联 RAG 与 Agent 模块，是系统默认使用的调度实现类
"""

import time
from typing import Dict, Any, Optional

from .base import BaseOrchestrator
from ..model.data_model import OrchestratorRequest, OrchestratorResponse
from ..utils.tool_functions import validate_request_params

# 依赖模块导入（遵循设计文档依赖关系）
from common_utils_module.core.impl import CommonUtils
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import OrchestratorException


class SimpleOrchestrator(BaseOrchestrator):
    """标准协同调度实现类：基于请求类型的路由 + 模块调用，系统默认实现"""

    def __init__(self, rag_runner=None, agent_runner=None):
        """
        初始化调度模块，加载系统配置，注册业务模块实例
        :param rag_runner: RAG 模块实例（需实现 BaseRAG 接口）
        :param agent_runner: Agent 模块实例（需实现 BaseAgent 接口）
        """
        # 基础支撑层初始化
        self.utils = CommonUtils()
        self.logger = SystemLogger()
        self.config = ConfigManager()
        self.config.load_config()

        # 业务模块注册表
        self.modules = {}
        if rag_runner:
            self.register_module("rag", rag_runner)
        if agent_runner:
            self.register_module("agent", agent_runner)

        # 读取系统调度核心配置
        self.default_type = self.config.get_config("orchestrator.default_type", "rag")
        self.timeout = int(self.config.get_config("orchestrator.timeout", 60))

        self.logger.info("协同调度模块初始化完成，加载系统默认配置")

    def route(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """实现抽象方法：路由决策与执行"""
        start_time = time.time()
        try:
            # 1. 参数校验
            req_type = request.get("type", self.default_type)
            if req_type not in ["rag", "agent", "hybrid"]:
                raise OrchestratorException(
                    "BAD_REQUEST",
                    f"不支持的 type：{req_type}"
                )

            # 2. 模块校验
            if req_type == "rag" and "rag" not in self.modules:
                raise OrchestratorException(
                    "MODULE_NOT_FOUND",
                    "RAG 模块未注册"
                )
            if req_type in ["agent", "hybrid"] and "agent" not in self.modules:
                raise OrchestratorException(
                    "MODULE_NOT_FOUND",
                    "Agent 模块未注册"
                )

            # 3. 执行路由
            if req_type == "rag":
                result = self._execute_rag(request)
            elif req_type == "agent":
                result = self._execute_agent(request)
            elif req_type == "hybrid":
                # Hybrid 模式通常由 Agent 主导，内部调用 RAG 工具
                result = self._execute_agent(request)

            # 4. 结果封装
            cost_time = time.time() - start_time
            return {
                "code": "SUCCESS",
                "message": "调度执行成功",
                "data": {
                    "route_type": req_type,
                    "result": result
                },
                "cost_time": cost_time
            }

        except OrchestratorException:
            raise
        except Exception as e:
            self.logger.error(f"调度执行失败：{str(e)}")
            raise OrchestratorException("ORCHESTRATOR_RUN_FAILED", str(e))

    def call_orchestrator(self, request: OrchestratorRequest) -> OrchestratorResponse:
        """实现抽象方法：标准化调度调用入口"""
        try:
            # 1. 模型转字典
            req_dict = {
                "type": request.type,
                "query": request.query,
                "task": request.task,
                "session_id": request.session_id,
                "top_k": request.top_k,
                "extra_params": request.extra_params
            }

            # 2. 调用路由
            result_dict = self.route(req_dict)

            # 3. 转换为响应模型
            trace_id = self.utils.get_assist_tool().get_current_time(
                format_="YYYYMMDDHHmmss"
            )
            response = OrchestratorResponse(
                code=result_dict["code"],
                message=result_dict["message"],
                data=result_dict.get("data"),
                route_type=result_dict["data"]["route_type"] if result_dict["data"] else None,
                cost_time=result_dict.get("cost_time"),
                trace_id=trace_id
            )
            return response

        except OrchestratorException as e:
            trace_id = self.utils.get_assist_tool().get_current_time(
                format_="YYYYMMDDHHmmss"
            )
            return OrchestratorResponse(
                code=e.code,
                message=e.message,
                data=None,
                trace_id=trace_id
            )
        except Exception as e:
            trace_id = self.utils.get_assist_tool().get_current_time(
                format_="YYYYMMDDHHmmss"
            )
            return OrchestratorResponse(
                code="ORCHESTRATOR_RUN_FAILED",
                message=str(e),
                data=None,
                trace_id=trace_id
            )

    def register_module(self, module_type: str, module_instance: Any) -> bool:
        """实现抽象方法：注册业务模块"""
        try:
            self.modules[module_type] = module_instance
            self.logger.info(f"业务模块注册成功：{module_type}")
            return True
        except Exception as e:
            self.logger.error(f"业务模块注册失败：{module_type}, {str(e)}")
            return False

    def _execute_rag(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """私有方法：执行 RAG 流程"""
        rag_module = self.modules["rag"]
        # 调用 RAG 模块 run 方法
        return rag_module.run(
            query=request["query"],
            top_k=request.get("top_k", 5)
        )

    def _execute_agent(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """私有方法：执行 Agent 流程"""
        agent_module = self.modules["agent"]
        # 调用 Agent 模块 execute 方法
        return agent_module.execute(
            task=request["task"],
            session_id=request.get("session_id")
        )