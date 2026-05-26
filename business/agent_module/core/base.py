# -*- coding: utf-8 -*-
"""
Agent 模块抽象基类

签名严格对齐 SimpleAgent 实际实现，请求统一采用 dict 风格（system 文档第 8 章定义
的统一请求格式），避免抽象/实现签名漂移导致上游做"签名探测"。
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable


class BaseAgent(ABC):
    """Agent 模块抽象基类，所有 Agent 实现类必须继承此类"""

    @abstractmethod
    def parse_task(
        self,
        task: str,
        session_id: str,
        trace_id: Optional[str] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """任务解析：将用户任务拆解为可执行的子步骤序列。

        返回结构:
            {"session_id": str, "task": str, "steps": List[Step]}
        每个 step 至少包含 step_id / tool_name / input_data。
        """

    @abstractmethod
    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """任务执行：执行完整任务流程，调用工具并汇总结果。

        参数:
            request: 统一请求 dict，含 task / session_id / trace_id / extra_params 等

        返回:
            统一响应信封 + Agent 数据载荷 (answer / steps / citations 等)
        """

    @abstractmethod
    def call_agent(
        self,
        task: str,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """关键字参数风格的便捷入口（等价于 execute，调用方无需手工构造 request dict）。"""

    @abstractmethod
    def register_tool(
        self,
        name: str,
        tool_func: Callable,
        description: str,
        input_schema: Dict,
    ) -> bool:
        """注册工具：动态注册新工具到 Agent 工具池。

        返回:
            注册成功 True，失败 False
        """

    @abstractmethod
    def unregister_tool(self, name: str) -> bool:
        """注销工具：从 Agent 工具池中移除工具。

        返回:
            注销成功 True，工具不存在或失败返回 False
        """
