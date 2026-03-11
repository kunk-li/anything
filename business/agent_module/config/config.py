# -*- coding: utf-8 -*-
"""
Agent 模块配置读取逻辑
读取全局配置，补充 Agent 专属配置
"""

from config_module.core.impl import ConfigManager


class AgentConfig:
    """Agent 模块专属配置类"""

    def __init__(self):
        self.config_manager = ConfigManager()
        self.config_manager.load_config()

    def get_max_retries(self) -> int:
        """获取工具调用最大重试次数"""
        return int(self.config_manager.get_config("agent.max_retries", 3))

    def get_timeout(self) -> int:
        """获取任务执行超时时间（秒）"""
        return int(self.config_manager.get_config("agent.timeout", 30))

    def get_default_session_prefix(self) -> str:
        """获取默认会话 ID 前缀"""
        return self.config_manager.get_config(
            "agent.session_prefix",
            "agent_session"
        )

    def get_state_expire_hours(self) -> int:
        """获取会话状态过期时间（小时）"""
        return int(self.config_manager.get_config(
            "agent.state_expire_hours",
            24
        ))

    def is_state_persist_enabled(self) -> bool:
        """是否启用状态持久化"""
        return self.config_manager.get_config(
            "agent.enable_state_persist",
            True
        )