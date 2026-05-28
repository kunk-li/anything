# -*- coding: utf-8 -*-
"""
audit_module — 审计日志 (Task CC #63), 从 common_utils_module 提到独立模块 (Task OO #75).

JSONL append-only audit log, 通过 hooks_module 的 post_tool_call / post_llm_call 钩子挂载.
"""

from .impl import (
    AuditLogger,
    get_audit_logger,
    reset_audit_logger,
    configure_audit_logger,
    install_audit_hooks,
)

__all__ = [
    "AuditLogger",
    "get_audit_logger",
    "reset_audit_logger",
    "configure_audit_logger",
    "install_audit_hooks",
]
