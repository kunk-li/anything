# -*- coding: utf-8 -*-
"""
Agent 模块抽象基类
定义模块核心接口，强制所有实现类必须实现，保障模块一致性、可替换性
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable

from ..model.data_model import AgentRequest, AgentResponse


class BaseAgent(ABC):
    """Agent 模块抽象基类，所有 Agent 实现类必须继承此类"""

    @abstractmethod
    def parse_task(self, task: str) -> Dict[str, Any]:
        """
        任务解析：将用户任务拆解为可执行的子步骤序列
        :param task: 用户任务描述
        :return: 任务计划（含子步骤列表）
        :raises AgentException: 解析失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        任务执行：执行完整任务流程，调用工具并汇总结果
        :param request: 请求信息
        :return: 标准化 Agent 响应结果（Dict 格式）
        :raises AgentException: 执行失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def call_agent(self, request: AgentRequest) -> AgentResponse:
        """
        统一 Agent 调用接口（对外标准化入口）
        :param request: Agent 请求模型
        :return: Agent 响应模型
        """
        pass

    @abstractmethod
    def register_tool(self, tool_name: str, tool_func: Callable,
                      description: str, input_schema: Dict) -> bool:
        """
        注册工具：动态注册新工具到 Agent 工具池
        :param tool_name: 工具名称
        :param tool_func: 工具 callable 函数
        :param description: 工具描述
        :param input_schema: 工具输入参数 schema
        :return: 注册成功返回 True，失败返回 False
        """
        pass

    @abstractmethod
    def unregister_tool(self, tool_name: str) -> bool:
        """
        注销工具：从 Agent 工具池中移除工具
        :param tool_name: 工具名称
        :return: 注销成功返回 True，失败返回 False
        """
        pass
