# -*- coding: utf-8 -*-
"""
协同调度模块配置读取逻辑
读取全局配置，补充调度专属配置
"""

from config_module.core.impl import ConfigManager


class OrchestratorConfig:
    """协同调度模块专属配置类"""

    def __init__(self):
        self.config_manager = ConfigManager()
        self.config_manager.load_config()

    def get_default_type(self) -> str:
        """获取默认请求类型"""
        return self.config_manager.get_config(
            "orchestrator.default_type",
            "rag"
        )

    def get_timeout(self) -> int:
        """获取调度超时时间（秒）"""
        return int(self.config_manager.get_config(
            "orchestrator.timeout",
            60
        ))

    def is_intelligent_route_enabled(self) -> bool:
        """是否启用智能意图识别路由"""
        return self.config_manager.get_config(
            "orchestrator.enable_intelligent_route",
            False
        )