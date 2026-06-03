# -*- coding: utf-8 -*-
"""方向4 第一级: 行为自反思 (建议性自主, 按需提议, 人审批才执行)。

验证: aggregate 聚合(成败率/错误码/成本) / reflect 产出提议 + 归一化 + fail-open /
agent.self_reflect 门控(关/无LLM/有LLM) / apply 仅落审批的 record_lesson、其余不动。
"""
import json
import os
import tempfile
import unittest

from agent_module.core.impl import SimpleAgent
from agent_module.core.components.self_reflection import (
    aggregate_audit_signals, SelfReflectionInspector,
)
from state_backend_module import InMemoryBackend
from long_term_memory_module.core.impl import LongTermMemoryImpl


class _Reg:
    def __init__(self): self._t = {}
    def register(self, n, f): self._t[n] = f
    def unregister(self, n): return self._t.pop(n, None) is not None
    def get(self, n): return self._t.get(n)
    def list_tools(self): return list(self._t.keys())


# ---------------- 纯逻辑: aggregate_audit_signals ----------------
class TestAggregate(unittest.TestCase):
    def test_per_tool_failure_and_codes(self):
        records = [
            {"event": "tool_call_finished", "tool": "rag_search", "success": True},
            {"event": "tool_call_finished", "tool": "http_request", "success": False, "code": "TOOL_CALL_FAILED"},
            {"event": "tool_call_finished", "tool": "http_request", "success": False, "code": "TOOL_CALL_FAILED"},
            {"event": "tool_call_finished", "tool": "http_request", "success": True},
            {"event": "llm_call_finished", "model": "x", "cost_usd": 0.01},
            {"event": "llm_call_finished", "model": "x", "cost_usd": 0.02},
        ]
        sig = aggregate_audit_signals(records)
        self.assertEqual(sig["records_analyzed"], 6)
        self.assertEqual(sig["tool_calls_finished"], 4)
        self.assertEqual(sig["tool_failures"], 2)
        self.assertEqual(sig["llm_calls"], 2)
        self.assertAlmostEqual(sig["total_cost_usd"], 0.03, places=4)
        top = sig["tools"][0]                       # 失败率最高排第一
        self.assertEqual(top["tool"], "http_request")
        self.assertEqual(top["failures"], 2)
        self.assertAlmostEqual(top["failure_rate"], 0.667, places=2)
        self.assertEqual(top["error_codes"], {"TOOL_CALL_FAILED": 2})

    def test_empty_records(self):
        sig = aggregate_audit_signals([])
        self.assertEqual(sig["records_analyzed"], 0)
        self.assertEqual(sig["tools"], [])

    def test_ignores_non_dict(self):
        sig = aggregate_audit_signals(
            [{"event": "tool_call_finished", "tool": "t", "success": True}, "not a dict", 123])
        self.assertEqual(sig["tool_calls_finished"], 1)


# ---------------- 纯逻辑: SelfReflectionInspector.reflect ----------------
class TestReflect(unittest.TestCase):
    _SIG = {"records_analyzed": 10, "tool_failures": 5,
            "tools": [{"tool": "http_request", "failure_rate": 0.8}]}

    def test_produces_proposals(self):
        def llm(p):
            return ('[{"problem":"http_request 失败率 80%","evidence":"5/?",'
                    '"proposed_action":"加重试或换工具","action_type":"record_lesson","severity":"high"}]')
        ps = SelfReflectionInspector(llm).reflect(self._SIG)
        self.assertEqual(len(ps), 1)
        self.assertEqual(ps[0].action_type, "record_lesson")
        self.assertEqual(ps[0].severity, "high")
        self.assertEqual(ps[0].id, "rp1")
        self.assertIn("http_request", ps[0].problem)

    def test_empty_signals_skips_llm(self):
        called = {"n": 0}
        def llm(p):
            called["n"] += 1
            return "[]"
        ps = SelfReflectionInspector(llm).reflect({"records_analyzed": 0})
        self.assertEqual(ps, [])
        self.assertEqual(called["n"], 0)            # 无信号 → 根本不调 LLM

    def test_llm_exception_fail_open(self):
        def boom(p):
            raise RuntimeError("down")
        self.assertEqual(SelfReflectionInspector(boom).reflect(self._SIG), [])

    def test_bad_json_fail_open(self):
        self.assertEqual(SelfReflectionInspector(lambda p: "不是 json").reflect(self._SIG), [])

    def test_normalizes_bad_enum(self):
        ps = SelfReflectionInspector(
            lambda p: '[{"problem":"x","action_type":"weird","severity":"nope"}]').reflect(self._SIG)
        self.assertEqual(ps[0].action_type, "advisory")
        self.assertEqual(ps[0].severity, "info")

    def test_skips_empty_problem(self):
        ps = SelfReflectionInspector(
            lambda p: '[{"problem":"","action_type":"advisory"},{"problem":"真问题","action_type":"advisory"}]'
        ).reflect(self._SIG)
        self.assertEqual(len(ps), 1)
        self.assertEqual(ps[0].problem, "真问题")


# ---------------- agent 接入: self_reflect 门控 + apply ----------------
class TestAgentSelfReflect(unittest.TestCase):
    def _agent(self, llm=None, enable=True):
        a = SimpleAgent(tool_registry=_Reg(), llm_planner=llm,
                        long_term_memory=LongTermMemoryImpl(backend=InMemoryBackend()))
        a.enable_self_reflection = enable
        return a

    def _audit_file(self):
        tf = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        tf.write(json.dumps({"event": "tool_call_finished", "tool": "t",
                             "success": False, "code": "X"}) + "\n")
        tf.close()
        return tf.name

    def test_disabled_returns_not_enabled(self):
        r = self._agent(enable=False).self_reflect()
        self.assertFalse(r["enabled"])
        self.assertEqual(r["proposals"], [])

    def test_enabled_no_llm_signals_only(self):
        a = self._agent(llm=None, enable=True)      # _Reg 无 llm_generate → 无 LLM 通道
        path = self._audit_file()
        try:
            r = a.self_reflect(audit_path=path)
            self.assertTrue(r["enabled"])
            self.assertEqual(r["signals"]["tool_failures"], 1)
            self.assertEqual(r["proposals"], [])     # 无 LLM → 给信号不反思
        finally:
            os.unlink(path)

    def test_enabled_with_llm_produces_proposals(self):
        def llm(p):
            return ('[{"problem":"工具 t 总失败","action_type":"record_lesson",'
                    '"proposed_action":"调用前先检查 t 可用"}]')
        a = self._agent(llm=llm, enable=True)
        path = self._audit_file()
        try:
            r = a.self_reflect(audit_path=path)
            self.assertTrue(r["enabled"])
            self.assertEqual(len(r["proposals"]), 1)
            self.assertEqual(r["proposals"][0]["action_type"], "record_lesson")
        finally:
            os.unlink(path)

    def test_apply_only_approved_record_lesson(self):
        a = self._agent(enable=True)
        proposals = [
            {"id": "rp1", "problem": "p1", "proposed_action": "教训一", "action_type": "record_lesson"},
            {"id": "rp2", "problem": "p2", "proposed_action": "建议改配置", "action_type": "config_suggestion"},
            {"id": "rp3", "problem": "p3", "proposed_action": "教训三", "action_type": "record_lesson"},
        ]
        # 审批 rp1(record_lesson, 落地) + rp2(config_suggestion, 不可自动落地); rp3 未审批
        res = a.apply_reflection_proposals(proposals, approved_ids=["rp1", "rp2"], tenant_id="t1")
        self.assertEqual(res["applied"], 1)          # 只有 rp1 落地
        facts = a.long_term_memory.list_facts("t1")
        self.assertEqual(len(facts), 1)
        self.assertIn("教训一", facts[0].content)
        self.assertEqual(facts[0].content_type, "convention")
        self.assertIn("self_reflection", facts[0].tags)

    def test_apply_nothing_approved_no_change(self):
        a = self._agent(enable=True)
        res = a.apply_reflection_proposals(
            [{"id": "rp1", "proposed_action": "x", "action_type": "record_lesson"}],
            approved_ids=[], tenant_id="t1")
        self.assertEqual(res["applied"], 0)
        self.assertEqual(len(a.long_term_memory.list_facts("t1")), 0)


if __name__ == "__main__":
    unittest.main()
