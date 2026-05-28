# -*- coding: utf-8 -*-
"""
DictToolRegistry — 最小的 in-memory 工具注册表实现 (Task RR #78 拆出).

给 SimpleAgent.tool_registry 注入用. register/get/describe 三件套.
"""
from __future__ import annotations

from typing import Any, Dict


class DictToolRegistry:
    """最小工具注册表实现，兼容 register/get 风格 + 工具描述(给 Agent LLM 规划用)"""

    def __init__(self):
        self._tools: Dict[str, Any] = {}
        self._descriptions: Dict[str, str] = {}

    def register(self, name: str, tool: Any, description: str = "") -> None:
        """注册工具. description 给 SimpleAgent._build_planner_prompt 用,
        没传时 Agent fallback 到内置硬编码 (rag_search / llm_generate)。"""
        self._tools[name] = tool
        if description:
            self._descriptions[name] = description

    def get(self, name: str) -> Any:
        return self._tools.get(name)

    def list_tools(self):
        return list(self._tools.keys())

    def describe(self, name: str) -> str:
        return self._descriptions.get(name, "")

    def describe_all(self) -> Dict[str, str]:
        return dict(self._descriptions)
