# -*- coding: utf-8 -*-
"""
Reflection 反思环测试 (Task III #95).

不依赖真实 LLM — 用 mock llm_planner.
"""
import os
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import unittest
from unittest.mock import MagicMock

from agent_module import SimpleAgent
from agent_module.core.impl import SimpleAgent as _SA


class _DummyTool:
    def __init__(self):
        self.tools = {}
        self.descs = {}

    def register(self, name, fn, description=""):
        self.tools[name] = fn
        if description:
            self.descs[name] = description

    def get(self, name):
        return self.tools.get(name)

    def list_tools(self):
        return list(self.tools.keys())

    def describe_all(self):
        return dict(self.descs)


# ============================================================
# _parse_reflection_json
# ============================================================


class TestParseReflectionJson(unittest.TestCase):

    def test_parses_clean_dict(self):
        raw = '{"issues": ["a", "b"], "overall_quality": 3}'
        obj = _SA._parse_reflection_json(raw)
        self.assertEqual(obj["issues"], ["a", "b"])
        self.assertEqual(obj["overall_quality"], 3)

    def test_parses_markdown_fenced(self):
        raw = '```json\n{"issues": ["x"], "should_revise": true}\n```'
        obj = _SA._parse_reflection_json(raw)
        self.assertEqual(obj["issues"], ["x"])
        self.assertTrue(obj["should_revise"])

    def test_parses_with_surrounding_text(self):
        raw = "Here is my critique:\n{\"issues\": [\"y\"]}\nThanks!"
        obj = _SA._parse_reflection_json(raw)
        self.assertEqual(obj["issues"], ["y"])

    def test_garbage_returns_none(self):
        self.assertIsNone(_SA._parse_reflection_json("not json"))

    def test_empty_returns_none(self):
        self.assertIsNone(_SA._parse_reflection_json(""))


# ============================================================
# _reflect_revise — 端到端
# ============================================================


class TestReflectRevise(unittest.TestCase):

    def _mock_llm(self, responses):
        """LLM 调用按顺序返预设. Each call is generate(prompt) → str."""
        it = iter(responses)
        return lambda prompt: next(it)

    def test_critique_then_revise_returns_new_answer(self):
        critique = '{"issues": ["缺细节"], "missing_info": [], "overall_quality": 2, "should_revise": true}'
        revised = "改进后的答案 (含细节)"
        agent = SimpleAgent(
            tool_registry=_DummyTool(),
            llm_planner=self._mock_llm([critique, revised]),
        )
        new_ans, meta = agent._reflect_revise(
            task="解释什么是 RAG", initial_answer="RAG 是检索 + 生成", trace_id="t1",
        )
        self.assertEqual(new_ans, "改进后的答案 (含细节)")
        self.assertEqual(meta["llm_calls"], 2)
        self.assertEqual(meta["n_issues"], 1)
        self.assertEqual(meta["overall_quality"], 2)
        self.assertIn("cost_ms", meta)

    def test_self_eval_good_skips_revise(self):
        """LLM 自评 quality>=4 且 should_revise=false → 跳过 revise 调用."""
        critique = '{"issues": [], "missing_info": [], "overall_quality": 5, "should_revise": false}'
        # 只准 1 个 response — 如果 _reflect_revise 调第 2 次 LLM 会 StopIteration
        agent = SimpleAgent(
            tool_registry=_DummyTool(),
            llm_planner=self._mock_llm([critique]),
        )
        new_ans, meta = agent._reflect_revise(
            task="x", initial_answer="完美答案", trace_id="t1",
        )
        self.assertIsNone(new_ans)  # 不需要 revise
        self.assertEqual(meta["llm_calls"], 1)
        self.assertEqual(meta["skipped_revise"], "self_eval_good")

    def test_no_llm_planner_returns_none(self):
        """没有 LLM 可用 (planner=None + tool_registry 没 llm_generate) → 跳过."""
        agent = SimpleAgent(tool_registry=_DummyTool(), llm_planner=None)
        new_ans, meta = agent._reflect_revise(
            task="x", initial_answer="y", trace_id="t1",
        )
        self.assertIsNone(new_ans)
        self.assertEqual(meta.get("skipped"), "no_llm")

    def test_critique_llm_failure(self):
        agent = SimpleAgent(
            tool_registry=_DummyTool(),
            llm_planner=MagicMock(side_effect=RuntimeError("LLM down")),
        )
        new_ans, meta = agent._reflect_revise(
            task="x", initial_answer="y", trace_id="t1",
        )
        self.assertIsNone(new_ans)
        self.assertEqual(meta["skipped"], "critique_llm_failed")
        self.assertIn("LLM down", meta["err"])

    def test_critique_garbage_returns_skip(self):
        agent = SimpleAgent(
            tool_registry=_DummyTool(),
            llm_planner=self._mock_llm(["not valid JSON garbage"]),
        )
        new_ans, meta = agent._reflect_revise(
            task="x", initial_answer="y", trace_id="t1",
        )
        self.assertIsNone(new_ans)
        self.assertEqual(meta["skipped"], "critique_json_parse_failed")

    def test_revise_llm_failure_keeps_critique_meta(self):
        critique = '{"issues": ["x"], "overall_quality": 2, "should_revise": true}'
        calls = [critique]
        def _mock(prompt):
            if calls:
                return calls.pop(0)
            raise RuntimeError("revise LLM down")
        agent = SimpleAgent(tool_registry=_DummyTool(), llm_planner=_mock)
        new_ans, meta = agent._reflect_revise(
            task="x", initial_answer="y", trace_id="t1",
        )
        self.assertIsNone(new_ans)
        self.assertEqual(meta["skipped"], "revise_llm_failed")
        # critique meta 仍保留
        self.assertEqual(meta["n_issues"], 1)
        self.assertEqual(meta["llm_calls"], 1)

    def test_revise_empty_response(self):
        critique = '{"issues": ["x"], "overall_quality": 2, "should_revise": true}'
        agent = SimpleAgent(
            tool_registry=_DummyTool(),
            llm_planner=self._mock_llm([critique, "   "]),
        )
        new_ans, meta = agent._reflect_revise(
            task="x", initial_answer="y", trace_id="t1",
        )
        self.assertIsNone(new_ans)
        self.assertEqual(meta["skipped"], "revise_empty")

    def test_non_numeric_quality_does_not_crash_and_revises(self):
        """LLM 返非数值 overall_quality (如 'high') → 不抛 ValueError, 降级为 0 → 仍 revise."""
        critique = '{"issues": ["x"], "overall_quality": "high", "should_revise": false}'
        revised = "改进版"
        agent = SimpleAgent(
            tool_registry=_DummyTool(),
            llm_planner=self._mock_llm([critique, revised]),
        )
        new_ans, meta = agent._reflect_revise(
            task="x", initial_answer="y", trace_id="t1",
        )
        # quality 非数值 → 降级 0 < 4 → 不跳过 → revise 正常返回
        self.assertEqual(new_ans, "改进版")
        self.assertEqual(meta["llm_calls"], 2)
        self.assertEqual(meta["overall_quality"], "high")  # 原值原样保留在 meta

    def test_quality_low_forces_revise_even_if_should_revise_false(self):
        """LLM 说 should_revise=false 但 quality<4 → 仍然 revise (gate 条件)."""
        critique = '{"issues": ["minor"], "overall_quality": 2, "should_revise": false}'
        revised = "改进版"
        agent = SimpleAgent(
            tool_registry=_DummyTool(),
            llm_planner=self._mock_llm([critique, revised]),
        )
        new_ans, meta = agent._reflect_revise(
            task="x", initial_answer="y", trace_id="t1",
        )
        self.assertEqual(new_ans, "改进版")
        self.assertEqual(meta["llm_calls"], 2)


if __name__ == "__main__":
    unittest.main()
