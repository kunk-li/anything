# -*- coding: utf-8 -*-
"""
请求响应处理模块初始化文件
暴露核心类与方法，方便外部模块调用
"""

from .core.base import BaseRequestHandler
from .core.impl import RequestHandler
from .model.data_model import UnifiedRequest, UnifiedResponse, ErrorDetails

__all__ = [
    "BaseRequestHandler",
    "RequestHandler",
    "UnifiedRequest",
    "UnifiedResponse",
    "ErrorDetails"
]

# 模块版本信息
__version__ = "1.0.0"
__author__ = "RAG-Agent System Team"