# -*- coding: utf-8 -*-
"""
deps_module: 基础支撑层依赖容器

提供 BasicDeps 容器与默认工厂，便于 bootstrap 一次构造、按引用注入到各业务/
接口/应用层模块的 __init__，避免每个模块都重复 new SystemLogger/ConfigManager
等基础组件。

设计偏离声明：
    本模块同 schema_module，不采用 core/base.py + impl.py 二分 —— BasicDeps
    是依赖打包容器，没有抽象/实现二分需求。
"""

from .deps import BasicDeps, build_basic_deps, StartupError, is_dev_mode

__all__ = ["BasicDeps", "build_basic_deps", "StartupError", "is_dev_mode"]
