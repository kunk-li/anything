# -*- coding: utf-8 -*-
"""
skills_module — Skill 系统 (Task AA #61), 从 common_utils_module 提到独立模块 (Task OO #75).

skills/*.md 自动注入到 Agent system prompt 的能力. 跨业务/接口/应用层共享.
"""

from .impl import (
    Skill,
    SkillRegistry,
    parse_skill_file,
    inject_skills_into_prompt,
    get_skill_registry,
    reset_skill_registry,
)

__all__ = [
    "Skill",
    "SkillRegistry",
    "parse_skill_file",
    "inject_skills_into_prompt",
    "get_skill_registry",
    "reset_skill_registry",
]
