# -*- coding: utf-8 -*-
"""
统一 bootstrap 组装入口 — entry points 集合.

Task RR (#78): 原 515 行混杂工厂拆到 factories/ 6 个文件
(tool_registry / basic_support / data / business / interface / application).
本文件现只剩 build_handler / build_api_app / build_console_app / build_all
4 个 entry points, 串接 factories 里的逐层装配函数.

老 import `from bootstrap import build_api_app` 仍可用 (本模块继续
re-export build_X_layer / DictToolRegistry / build_basic_support 等符号).
"""
from typing import Any, Dict

from deps_module import build_basic_deps
from request_response_module import RequestHandler
from console_app_module.core.impl import ConsoleApp

# Task RR (#78): 实现拆到 factories/, 此处 re-export 保持老 import 链路
from factories import (
    DictToolRegistry,
    build_basic_support,
    _build_component,
    build_data_layer,
    build_business_layer,
    build_interface_layer,
    build_application_layer,
)


def build_handler() -> RequestHandler:
    """直接构建统一 RequestHandler（DI 共享一份 BasicDeps）"""
    deps = build_basic_deps()
    business_layer = build_business_layer(deps=deps)
    interface_layer = build_interface_layer(business_layer=business_layer, deps=deps)
    return interface_layer["handler"]


def build_api_app():
    """构建 FastAPI app（DI 共享一份 BasicDeps）"""
    deps = build_basic_deps()
    # 透传 data_layer 让 /config/models 端点能拿到 LLMService 实例
    data_layer = build_data_layer(deps=deps)
    business_layer = build_business_layer(data_layer=data_layer, deps=deps)
    interface_layer = build_interface_layer(business_layer=business_layer, deps=deps)
    app_layer = build_application_layer(
        interface_layer=interface_layer,
        build_api=True,
        build_console=False,
        deps=deps,
        data_layer=data_layer,
        business_layer=business_layer,
    )
    return app_layer["api_service"].app


def build_console_app() -> ConsoleApp:
    """构建控制台应用（DI 共享一份 BasicDeps）"""
    deps = build_basic_deps()
    app_layer = build_application_layer(build_api=False, build_console=True, deps=deps)
    return app_layer["console_app"]


def build_all(include_console: bool = False) -> Dict[str, Any]:
    """完整构建所有层（基础依赖 DI 共享,便于调试或测试）"""
    basic = build_basic_support()
    deps = basic["deps"]  # 由 build_basic_support 暴露的容器
    data = build_data_layer(deps=deps)
    business = build_business_layer(data_layer=data, deps=deps)
    interface = build_interface_layer(business_layer=business, deps=deps)
    app = build_application_layer(
        interface_layer=interface,
        build_api=True,
        build_console=include_console,
        deps=deps,
        data_layer=data,
        business_layer=business,
    )

    return {
        "basic_support": basic,
        "data_layer": data,
        "business_layer": business,
        "interface_layer": interface,
        "application_layer": app,
    }


__all__ = [
    # entry points
    "build_handler",
    "build_api_app",
    "build_console_app",
    "build_all",
    # back-compat re-exports from factories/
    "DictToolRegistry",
    "build_basic_support",
    "_build_component",
    "build_data_layer",
    "build_business_layer",
    "build_interface_layer",
    "build_application_layer",
]
