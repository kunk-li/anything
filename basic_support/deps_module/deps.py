# -*- coding: utf-8 -*-
"""
BasicDeps: 基础支撑层依赖容器

包含 ConfigManager / SystemLogger / CommonUtils / ExceptionHandler 四个基础组件
的单一实例引用。bootstrap 在装配阶段创建一次，向各上层模块注入；
各模块的 __init__ 优先使用注入的 deps，未注入时回退为自行构造（向后兼容）。

为什么不用 frozen=True dataclass?
    部分组件构造时会做"延迟加载配置 / 初始化日志句柄"等副作用，外部代码可能在
    构造后再次调用 load。冻结实例会阻断这种合法使用，因此采用普通 dataclass。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class BasicDeps:
    """基础支撑层依赖容器。

    Fields:
        config: ConfigManager 实例（已 load_config / load）
        logger: SystemLogger 实例
        utils: CommonUtils 实例
        exception_handler: ExceptionHandler 实例
    """

    config: Any
    logger: Any
    utils: Any
    exception_handler: Any


def build_basic_deps() -> BasicDeps:
    """工厂方法：按默认方式构造一套 BasicDeps。

    本方法是 bootstrap.build_basic_support 的功能等价物，但返回结构化容器
    便于直接注入下游模块；bootstrap 应优先调用本方法。

    注意：import 延迟到函数内，避免模块导入期触发 ConfigManager 加载，
    单测时可只 import BasicDeps 而不触发实例化。
    """
    from common_utils_module.core.impl import CommonUtils
    from config_module.core.impl import ConfigManager
    from log_module.core.impl import SystemLogger
    from exception_module.core.impl import ExceptionHandler

    config = ConfigManager()
    # ConfigManager 在历史上同时存在 load_config / load 两种方法名,兼容处理
    if hasattr(config, "load_config"):
        config.load_config()
    elif hasattr(config, "load"):
        config.load()

    return BasicDeps(
        config=config,
        logger=SystemLogger(),
        utils=CommonUtils(),
        exception_handler=ExceptionHandler(),
    )
