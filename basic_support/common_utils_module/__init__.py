"""common_utils_module 模块入口。

暴露 CommonUtils 供上层模块直接导入使用：
    from common_utils_module import CommonUtils

Task U (#55): 同时暴露 ProjectMemory — Agent/RAG init 时注入项目级记忆.
"""

from .core.impl import CommonUtils
from .utils.project_memory import ProjectMemory, get_project_memory, reset_project_memory
from .utils.hooks import (
    BlockedError,
    HookRegistry,
    get_hook_registry,
    reset_hook_registry,
)
from .utils.skills import (
    Skill,
    SkillRegistry,
    get_skill_registry,
    reset_skill_registry,
    inject_skills_into_prompt,
)
from .utils.quota import (
    QuotaGuard,
    get_quota_guard,
    reset_quota_guard,
    configure_quota,
    install_quota_hooks,
)
from .utils.audit_log import (
    AuditLogger,
    get_audit_logger,
    reset_audit_logger,
    configure_audit_logger,
    install_audit_hooks,
)

__all__ = [
    "CommonUtils",
    "ProjectMemory", "get_project_memory", "reset_project_memory",
    "BlockedError", "HookRegistry", "get_hook_registry", "reset_hook_registry",
    "Skill", "SkillRegistry", "get_skill_registry", "reset_skill_registry",
    "inject_skills_into_prompt",
    "QuotaGuard", "get_quota_guard", "reset_quota_guard",
    "configure_quota", "install_quota_hooks",
    "AuditLogger", "get_audit_logger", "reset_audit_logger",
    "configure_audit_logger", "install_audit_hooks",
]
