"""
config_module 模块入口

对外暴露 ConfigManager，供其他模块统一导入使用：
    from config_module import ConfigManager
"""
from .core.base import BaseConfigManager  # noqa: F401
from .core.impl import ConfigManager  # noqa: F401
