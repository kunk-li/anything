# -*- coding: utf-8 -*-
"""
common_utils_module.utils.project_memory — back-compat re-export shim (Task OO #75).

实际实现已搬到 project_memory_module/.
"""

from project_memory_module.impl import (
    ProjectMemory,
    get_project_memory,
    reset_project_memory,
    load_project_memory_for_root,
)

__all__ = [
    "ProjectMemory",
    "get_project_memory",
    "reset_project_memory",
    "load_project_memory_for_root",
]
