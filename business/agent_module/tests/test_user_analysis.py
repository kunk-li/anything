# -*- coding: utf-8 -*-
"""用户分析流程 (analyze_user): 聚合/Inspector/analyze_user/apply_user_insights/每N轮自动。"""
import unittest

from agent_module.core.impl import SimpleAgent
from agent_module.core.components.user_analysis import (
    aggregate_user_signals, UserAnalysisInspector,
)
from long_term_memory_module import LongTermMemoryImpl, Fact
from state_backend_module import InMemoryBackend


class _Reg:
    def __init__(self): self._t = {}
    def register(self, n, f): self._t[n] = f
    def unregister(self, n): return self._t.pop(n, None) is not None
    def get(self, n): return self._t.get(n)
    def list_tools(self): return list(self._t.keys())


def _agent(llm=None, enabled=True):
    a = SimpleAgent(tool_registry=_Reg(), llm_planner=llm,
                    long_term_memory=LongTermMemoryImpl(backend=InMemoryBackend()))
    a.enable_user_analysis = enabled
    return a


class TestAggregate(unittest.TestCase):
    def test_counts_and_samples(self):
        facts = [Fact.make("偏好类型标注", content_type="preference"),
                 Fact.make("Python 后端", content_type="domain")]
        s = aggregate_user_signals(facts)
        self.assertEqual(s["total_facts"], 2)
        self.assertEqual(s["by_type"]["preference"], 1)
        self.assertEqual(len(s["samples"]), 2)


class TestInspector(unittest.TestCase):
    def test_empty_signals(self):
        self.assertEqual(UserAnalysisInspector(lambda p: "").analyze({"total_facts": 0}),
                         {"insights": [], "proposals": []})

    def test_parses_insights_and_proposals(self):
        resp = ('{"insights": ["你常写带类型标注的 Python"], '
                '"proposals": [{"dim": "domain", "content": "Python 后端开发", "reason": "多条 python fact"}]}')
        out = UserAnalysisInspector(lambda p: resp).analyze({"total_facts": 3, "samples": []})
        self.assertEqual(out["insights"], ["你常写带类型标注的 Python"])
        self.assertEqual(out["proposals"][0].dim, "domain")

    def test_invalid_dim_skipped(self):
        resp = '{"insights": [], "proposals": [{"dim": "bogus", "content": "x"}]}'
        out = UserAnalysisInspector(lambda p: resp).analyze({"total_facts": 1, "samples": []})
        self.assertEqual(out["proposals"], [])

    def test_llm_exception_fail_open(self):
        def boom(p):
            raise RuntimeError("llm down")
        self.assertEqual(UserAnalysisInspector(boom).analyze({"total_facts": 1, "samples": []}),
                         {"insights": [], "proposals": []})


class TestAnalyzeUser(unittest.TestCase):
    def test_default_off(self):
        a = _agent(enabled=False)
        r = a.analyze_user()
        self.assertIs(r["enabled"], False)

    def test_on_returns_insights_and_proposals(self):
        resp = ('{"insights": ["偏好类型标注+测试"], '
                '"proposals": [{"dim": "preference", "content": "要类型标注和单测", "reason": "r"}]}')
        a = _agent(llm=(lambda p: resp))
        a.long_term_memory.add_fact(Fact.make("用 Python 带类型标注", tenant_id="default", content_type="preference"))
        r = a.analyze_user()
        self.assertTrue(r["enabled"])
        self.assertTrue(r["insights"])
        self.assertEqual(r["proposals"][0]["dim"], "preference")

    def test_no_llm_only_signals(self):
        a = _agent(llm=None)
        a.long_term_memory.add_fact(Fact.make("x", tenant_id="default", content_type="domain"))
        r = a.analyze_user()
        self.assertTrue(r["enabled"])
        self.assertEqual(r["proposals"], [])         # 无 LLM → 不分析


class TestApplyInsights(unittest.TestCase):
    def test_applies_approved_skips_invalid(self):
        a = _agent()
        props = [{"id": "ua_0", "dim": "domain", "content": "Python 后端开发"},
                 {"id": "ua_1", "dim": "bogus", "content": "x"}]
        r = a.apply_user_insights(props, ["ua_0", "ua_1"])
        self.assertEqual(r["applied"], 1)            # bogus dim 跳过
        prof = a.long_term_memory.get_user_profile("default")
        self.assertIn("domain", prof)                # 反哺画像生效

    def test_unapproved_not_applied(self):
        a = _agent()
        props = [{"id": "ua_0", "dim": "domain", "content": "X"}]
        r = a.apply_user_insights(props, [])         # 没批准
        self.assertEqual(r["applied"], 0)


class TestAutoTrigger(unittest.TestCase):
    def test_off_by_default(self):
        a = _agent()
        a.user_analysis_every_n = 0
        a._maybe_auto_user_analysis("default")
        self.assertEqual(getattr(a, "_user_analysis_counter", 0), 0)

    def test_counter_and_reset(self):
        a = _agent(llm=None)
        a.user_analysis_every_n = 3
        a._maybe_auto_user_analysis("default")
        a._maybe_auto_user_analysis("default")
        self.assertEqual(a._user_analysis_counter, 2)
        a._maybe_auto_user_analysis("default")       # 命中阈值 → 重置 + 后台跑
        self.assertEqual(a._user_analysis_counter, 0)


if __name__ == "__main__":
    unittest.main()
