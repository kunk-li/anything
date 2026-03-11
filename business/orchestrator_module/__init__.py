# -*- coding: utf-8 -*-
"""
协同调度模块初始化文件
暴露核心类与方法，方便外部模块调用
"""

from .core.base import BaseOrchestrator
from .core.impl import SimpleOrchestrator
from .model.data_model import OrchestratorRequest, OrchestratorResponse

__all__ = [
    "BaseOrchestrator",
    "SimpleOrchestrator",
    "OrchestratorRequest",
    "OrchestratorResponse"
]

# 模块版本信息
__version__ = "1.0.0"
__author__ = "RAG-Agent System Team"