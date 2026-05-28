# -*- coding: utf-8 -*-
"""
common_utils_module.utils.hooks — back-compat re-export shim (Task OO #75).

实际实现已搬到 hooks_module/. 老的深度 import 仍可用:
    from common_utils_module.utils.hooks import HookRegistry, BlockedError
也推荐用 `from hooks_module import ...` 或 `from common_utils_module import ...`.
"""

from hooks_module.impl import (
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
