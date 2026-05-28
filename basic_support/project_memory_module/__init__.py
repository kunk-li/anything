# -*- coding: utf-8 -*-
"""
project_memory_module — 项目级记忆 (Task U #55), 从 common_utils_module 提到独立模块 (Task OO #75).

AGENTS.md 注入 Agent system prompt + RAG context. 跨业务/接口/应用层共享.
"""

from .impl import (
    ProjectMemory,
    get_project_memory,
    reset_project_memory,
)

__all__ = [
    "ProjectMemory",
    "get_project_memory",
    "reset_project_memory",
]
