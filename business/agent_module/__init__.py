# -*- coding: utf-8 -*-
"""
Agent 模块对外暴露

仅导出抽象基类 + 默认实现。
统一请求/响应数据契约请用 schema_module.RequestEnvelope / ResponseEnvelope,
不再保留 AgentRequest / AgentResponse / TaskStep / TaskPlan / ToolResult /
StateEvent 旧 dataclass(已删除,原本是死代码)。
"""

from .core.base import BaseAgent
from .core.impl import SimpleAgent

__all__ = [
    "BaseAgent",
    "SimpleAgent",
]
