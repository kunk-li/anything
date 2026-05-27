# -*- coding: utf-8 -*-
"""单测 — Task #36 工具描述机制升级:
SimpleAgent 应优先用 registry.describe* 返回的描述, 没有时 fallback 到内置 tool_docs。
"""

import os
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import unittest
from unittest.mock import MagicMock

from agent_module.core.impl import SimpleAgent


class _RegistryWithDescribe:
    def __init__(self, tools, descriptions):
        self._tools = tools
        self._descriptions = descriptions

    def register(self, *a, **kw): pass
    def get(self, name): return self._tools.get(name)
    def list_tools(self): return list(self._tools.keys())
    def describe(self, name): return self._descriptions.get(name, "")
    def describe_all(self): return dict(self._descriptions)


class _RegistryWithoutDescribe:
    def __init__(self, tools):
        self._tools = tools

    def register(self, *a, **kw): pass
    def get(self, name): return self._tools.get(name)
    def list_tools(self): return list(self._tools.keys())


class TestToolDescriptionsPath(unittest.TestCase):

    def _make_agent(self, registry):
        return SimpleAgent(
            state_store=MagicMock(),
            tool_registry=registry,
            timeout=10,
            max_retries=1,
            session_prefix="test",
        )

    def test_planner_prompt_uses_registry_descriptions(self):
        """registry 提供的描述应出现在 LLM prompt 中"""
        registry = _RegistryWithDescribe(
            tools={"calculator": lambda p: {}, "rag_search": lambda p: {}},
            descriptions={
                "calculator": "做数学运算的工具, 不联网",
                "rag_search": "在知识库检索",
            },
        )
        agent = self._make_agent(registry)
        descs = agent._tool_descriptions()
        self.assertEqual(descs["calculator"], "做数学运算的工具, 不联网")
        self.assertEqual(descs["rag_search"], "在知识库检索")

        prompt = SimpleAgent._build_planner_prompt(
            task="算 1+1",
            available_tools=["calculator", "rag_search"],
            tool_descriptions=descs,
        )
        self.assertIn("做数学运算的工具, 不联网", prompt)
        self.assertIn("calculator", prompt)

    def test_planner_falls_back_to_builtin_docs(self):
        """registry 不支持 describe 时, 应 fallback 到内置硬编码"""
        registry = _RegistryWithoutDescribe(
            tools={"rag_search": lambda p: {}, "llm_generate": lambda p: {}},
        )
        agent = self._make_agent(registry)
        descs = agent._tool_descriptions()
        # 拿不到, 应是空 dict
        self.assertEqual(descs, {})

        prompt = SimpleAgent._build_planner_prompt(
            task="x",
            available_tools=["rag_search", "llm_generate"],
            tool_descriptions={},
        )
        # 内置 fallback 文案应出现
        self.assertIn("知识库中检索", prompt)
        self.assertIn("大语言模型生成文本", prompt)

    def test_react_prompt_uses_registry_descriptions(self):
        """_build_react_prompt 也应支持 tool_descriptions 参数"""
        prompt = SimpleAgent._build_react_prompt(
            task="t",
            available_tools=["calculator"],
            history=[],
            iteration=1,
            max_iterations=3,
            tool_descriptions={"calculator": "do math"},
        )
        self.assertIn("do math", prompt)
        self.assertIn("calculator", prompt)

    def test_unknown_tool_falls_back_to_dash(self):
        """既没 registry 描述也没内置兜底的工具应该显示 '(无描述)'"""
        prompt = SimpleAgent._build_planner_prompt(
            task="t",
            available_tools=["mystery_tool"],
            tool_descriptions={},
        )
        self.assertIn("mystery_tool", prompt)
        self.assertIn("(无描述)", prompt)


if __name__ == "__main__":
    unittest.main()
