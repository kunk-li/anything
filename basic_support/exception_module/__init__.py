"""exception_module - 基础支撑层异常处理模块

对外暴露：
- ExceptionHandler: 异常处理器实现
- BaseExceptionHandler: 抽象接口
- SystemBaseException 及各业务异常：ConfigException/VectorDBException/RAGException/AgentException
"""

from .core.base import BaseExceptionHandler
from .core.impl import (
    ExceptionHandler,
    SystemBaseException,
    ConfigException,
    VectorDBException,
    RAGException,
    AgentException,
)

__all__ = [
    "BaseExceptionHandler",
    "ExceptionHandler",
    "SystemBaseException",
    "ConfigException",
    "VectorDBException",
    "RAGException",
    "AgentException",
]
