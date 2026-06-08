# -*- coding: utf-8 -*-
"""模型分级路由 (执行计划③): 复杂度分类 / 选模型 / begin-end_routing 写读 ContextVar。"""
import unittest

from agent_module.core.model_routing import (
    classify_task_complexity, pick_model, begin_routing, end_routing,
)
from observability_module import get_model_routing


class _FakeAgent:
    def __init__(self, enabled=True, simple="qwen-plus", complex="qwen-max", budget=0):
        self.model_routing_enabled = enabled
        self.model_simple = simple
        self.model_complex = complex
        self.max_task_tokens = budget


class TestClassify(unittest.TestCase):
    def test_short_simple(self):
        self.assertEqual(classify_task_complexity("现在几点"), "simple")
        self.assertEqual(classify_task_complexity("用一句话解释 RAG"), "simple")

    def test_empty_simple(self):
        self.assertEqual(classify_task_complexity(""), "simple")
        self.assertEqual(classify_task_complexity(None), "simple")

    def test_long_complex(self):
        self.assertEqual(classify_task_complexity("详细" * 40), "complex")

    def test_keyword_complex(self):
        self.assertEqual(classify_task_complexity("帮我分析这个错误"), "complex")
        self.assertEqual(classify_task_complexity("先查天气然后算个数"), "complex")
        self.assertEqual(classify_task_complexity("写一个快速排序"), "complex")


class TestPick(unittest.TestCase):
    def test_simple_complex(self):
        self.assertEqual(pick_model("simple", "plus", "max"), "plus")
        self.assertEqual(pick_model("complex", "plus", "max"), "max")

    def test_empty_returns_none(self):
        self.assertIsNone(pick_model("simple", "", ""))
        self.assertIsNone(pick_model("complex", "", None))


class TestBeginEndRouting(unittest.TestCase):
    def test_disabled_returns_none(self):
        tok = begin_routing(_FakeAgent(enabled=False), "现在几点")
        self.assertIsNone(tok)
        self.assertEqual(get_model_routing(), (None, None))

    def test_simple_routes_cheap(self):
        tok = begin_routing(_FakeAgent(enabled=True), "现在几点")  # simple
        try:
            self.assertEqual(get_model_routing()[0], "qwen-plus")
        finally:
            end_routing(tok)
        self.assertEqual(get_model_routing(), (None, None))   # 重置回默认

    def test_complex_routes_strong(self):
        tok = begin_routing(_FakeAgent(enabled=True), "帮我分析并规划整个项目")  # complex
        try:
            self.assertEqual(get_model_routing()[0], "qwen-max")
        finally:
            end_routing(tok)

    def test_budget_only_still_routes(self):
        # 两档模型空但有 token 预算 → 仍路由 (model=None, max_tokens=预算)
        tok = begin_routing(_FakeAgent(enabled=True, simple="", complex="", budget=500), "现在几点")
        try:
            self.assertEqual(get_model_routing(), (None, 500))
        finally:
            end_routing(tok)
        self.assertIsNotNone(tok)

    def test_nothing_to_route_returns_none(self):
        # 开启但无模型、无预算 → 不路由
        tok = begin_routing(_FakeAgent(enabled=True, simple="", complex="", budget=0), "现在几点")
        self.assertIsNone(tok)


if __name__ == "__main__":
    unittest.main()
