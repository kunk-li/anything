# -*- coding: utf-8 -*-
"""
factories/ — bootstrap 拆出的工厂集合 (Task RR #78)

run/bootstrap.py 原 515 行单文件混杂 5 层 + 工具注册表, 按层拆为 6 文件:
    tool_registry.py     DictToolRegistry  (跨 business / agent 用)
    basic_support.py     build_basic_support + _build_component
    data_layer.py        build_data_layer
    business_layer.py    build_business_layer (含 18 个工具注册)
    interface_layer.py   build_interface_layer
    application_layer.py build_application_layer

bootstrap.py 现只剩 entry points (build_handler / build_api_app /
build_console_app / build_all), 各自调 factories 里的逐层装配函数.
"""

from .tool_registry import DictToolRegistry
from .basic_support import build_basic_support, _build_component
from .data_layer import build_data_layer
from .business_layer import build_business_layer
from .interface_layer import build_interface_layer
from .application_layer import build_application_layer

__all__ = [
    "DictToolRegistry",
    "build_basic_support",
    "_build_component",
    "build_data_layer",
    "build_business_layer",
    "build_interface_layer",
    "build_application_layer",
]
