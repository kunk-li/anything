# -*- coding: utf-8 -*-
"""
build_basic_support + _build_component (Task RR #78 拆出).

基础层装配 + 组件构造保护 (生产 fail-fast / dev_mode 回退 None).
"""
from __future__ import annotations

from typing import Any, Dict

from deps_module import build_basic_deps, StartupError, is_dev_mode


def build_basic_support() -> Dict[str, Any]:
    """构建基础支撑层（基于 BasicDeps 容器,内部单例共享）。

    返回 dict 以保持向后兼容;若调用方需要容器引用,推荐改用 build_basic_deps()。
    """
    deps = build_basic_deps()
    return {
        "config": deps.config,
        "logger": deps.logger,
        "exception_handler": deps.exception_handler,
        "utils": deps.utils,
        "deps": deps,  # 暴露容器本身,便于业务/接口/应用层注入
    }


def _build_component(name: str, factory, hint: str = "") -> Any:
    """构建单个组件,失败时根据 dev_mode 决定是抛 StartupError 还是返回 None。

    生产/严格模式(默认): 抛 StartupError,系统拒绝启动
    开发模式 (ANYTHING_DEV_MODE=1): 打印 WARNING 并返回 None,允许下游用占位
    """
    try:
        return factory()
    except Exception as e:
        if is_dev_mode():
            print(f"[bootstrap][DEV_MODE] {name} 初始化失败,回退 None: {e}")
            return None
        raise StartupError(
            component=name,
            reason=str(e),
            hint=hint or "设置环境变量 ANYTHING_DEV_MODE=1 可在本地用占位实现继续运行",
        ) from e
