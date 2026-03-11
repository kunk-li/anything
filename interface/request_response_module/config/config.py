# -*- coding: utf-8 -*-
"""
请求响应处理模块配置读取逻辑
读取全局配置，补充处理专属配置
"""

from config_module.core.impl import ConfigManager


class RequestResponseConfig:
    """请求响应处理模块专属配置类"""

    def __init__(self):
        self.config_manager = ConfigManager()
        self.config_manager.load_config()

    def get_default_type(self) -> str:
        """获取默认请求类型"""
        return self.config_manager.get_config(
            "request_response.default_type",
            "rag"
        )

    def is_trace_enabled(self) -> bool:
        """是否启用链路追踪"""
        return self.config_manager.get_config(
            "request_response.enable_trace",
            True
        )

    def get_max_request_size(self) -> int:
        """获取最大请求大小（字节）"""
        return int(self.config_manager.get_config(
            "request_response.max_request_size",
            1048576  # 1MB
        ))

    def get_timeout(self) -> int:
        """获取请求处理超时时间（秒）"""
        return int(self.config_manager.get_config(
            "request_response.timeout",
            60
        ))

    def is_validate_strict(self) -> bool:
        """是否启用严格参数校验"""
        return self.config_manager.get_config(
            "request_response.validate_strict",
            True
        )