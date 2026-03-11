# -*- coding: utf-8 -*-
"""
协同调度模块抽象基类
定义模块核心接口，强制所有实现类必须实现，保障模块一致性、可替换性
"""

from abc import ABC, abstractmethod
from typing import Dict, Any

from ..model.data_model import OrchestratorRequest, OrchestratorResponse


class BaseOrchestrator(ABC):
    """协同调度模块抽象基类，所有调度实现类必须继承此类"""

    @abstractmethod
    def route(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        路由决策与执行：根据请求内容路由到 RAG/Agent/协同，并执行对应流程
        :param request: 原始请求字典（含 type, query, task 等）
        :return: 标准化响应结果（Dict 格式）
        :raises OrchestratorException: 路由失败或执行失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def call_orchestrator(self, request: OrchestratorRequest) -> OrchestratorResponse:
        """
        统一调度调用接口（对外标准化入口）
        :param request: 调度请求模型
        :return: 调度响应模型
        """
        pass

    @abstractmethod
    def register_module(self, module_type: str, module_instance: Any) -> bool:
        """
        注册业务模块：动态注册 RAG 或 Agent 实例到调度器
        :param module_type: 模块类型（rag/agent）
        :param module_instance: 模块实例（需实现 BaseRAG 或 BaseAgent 接口）
        :return: 注册成功返回 True，失败返回 False
        """
        pass