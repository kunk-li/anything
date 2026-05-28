# -*- coding: utf-8 -*-
"""
quota_module — 配额 / 限流 (Task BB #62), 从 common_utils_module 提到独立模块 (Task OO #75).

per-tenant USD + rate limit, 通过 hooks_module 的 pre_llm_call / pre_tool_call 钩子挂载.
"""

from .impl import (
    QuotaGuard,
    get_quota_guard,
    reset_quota_guard,
    configure_quota,
    install_quota_hooks,
)

__all__ = [
    "QuotaGuard",
    "get_quota_guard",
    "reset_quota_guard",
    "configure_quota",
    "install_quota_hooks",
]
