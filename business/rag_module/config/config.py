# -*- coding: utf-8 -*-
"""
RAG 模块配置读取逻辑
读取全局配置，补充 RAG 专属配置
"""

from config_module.core.impl import ConfigManager


class RAGConfig:
    """RAG 模块专属配置类"""

    def __init__(self):
        self.config_manager = ConfigManager()
        self.config_manager.load_config()

    def get_default_top_k(self) -> int:
        """获取默认检索数量"""
        return int(self.config_manager.get_config("rag.default_top_k", 5))

    def get_max_context_length(self) -> int:
        """获取上下文最大长度"""
        return int(self.config_manager.get_config("rag.max_context_length", 4096))

    def get_context_truncate_length(self) -> int:
        """获取片段截断长度"""
        return int(self.config_manager.get_config("rag.context_truncate_length", 1200))