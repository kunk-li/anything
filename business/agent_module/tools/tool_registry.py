# -*- coding: utf-8 -*-
"""
Agent 工具注册表
管理所有可用工具，支持动态注册与注销
"""

from typing import Dict, Any, Callable, Optional


class ToolRegistry:
    """Agent 工具注册表，管理所有可用工具"""

    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register(self, tool_name: str, tool_func: Callable,
                 description: str = "", input_schema: Optional[Dict] = None):
        """
        注册工具
        :param tool_name: 工具名称
        :param tool_func: 工具 callable 函数
        :param description: 工具描述
        :param input_schema: 工具输入参数 schema
        """
        self._tools[tool_name] = {
            "func": tool_func,
            "description": description,
            "input_schema": input_schema or {}
        }

    def unregister(self, tool_name: str):
        """
        注销工具
        :param tool_name: 工具名称
        """
        if tool_name in self._tools:
            del self._tools[tool_name]

    def get(self, tool_name: str) -> Optional[Callable]:
        """
        获取工具函数
        :param tool_name: 工具名称
        :return: 工具函数，不存在返回 None
        """
        tool_info = self._tools.get(tool_name)
        return tool_info["func"] if tool_info else None

    def list_tools(self) -> Dict[str, Dict]:
        """
        列出所有已注册工具
        :return: 工具信息字典
        """
        return {
            name: {
                "description": info["description"],
                "input_schema": info["input_schema"]
            }
            for name, info in self._tools.items()
        }