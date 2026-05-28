# -*- coding: utf-8 -*-
"""
hooks_module — Hooks 系统 (Task Z #60), 从 common_utils_module 提到独立模块 (Task OO #75).

Claude Code 风格 hook framework, pre/post tool/llm call 4 类钩子.
跨业务/接口/应用层共享, 是 cross-cutting concern → 独立 module.

老 import `from common_utils_module import BlockedError, get_hook_registry`
仍可用 (common_utils_module/__init__.py 仍 re-export 它们).
"""

from .impl import (
    BlockedError,
    HookFn,
    HookRegistry,
    get_hook_registry,
    reset_hook_registry,
)

__all__ = [
    "BlockedError",
    "HookFn",
    "HookRegistry",
    "get_hook_registry",
    "reset_hook_registry",
]
