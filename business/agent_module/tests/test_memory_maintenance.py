# -*- coding: utf-8 -*-
"""方向4 扩域·记忆健康自维护: 只读检视 → 确定性提议 → 人审批 → 复用现成算子。

验证: aggregate 计数(陈旧/降级/superseded, pinned&canonical 排除) / propose 阈值规则 /
agent propose 门控 / apply 仅审批项映射到算子(真删陈旧) + 未审批不动。
"""
import time
import unittest

from agent_module.core.impl import SimpleAgent
from agent_module.core.components.self_reflection import (
    aggregate_memory_signals, propose_from_memory_signals,
)
from state_backend_module import InMemoryBackend
from long_term_memory_module.core.impl import LongTermMemoryImpl
from long_term_memory_module.model import Fact


class _Reg:
    def __init__(self): self._t = {}
    def register(self, n, f): self._t[n] = f
    def unregister(self, n): return self._t.pop(n, None) is not None
    def get(self, n): return self._t.get(n)
    def list_tools(self): return list(self._t.keys())


NOW = 1_000_000_000.0
OLD = NOW - 100 * 86400          # 100 天前 (> 90)


class TestAggregateMemory(unittest.TestCase):
    def _facts(self):
        return [
            Fact.make("old1", last_accessed=OLD, access_count=0, mutability="refinable"),                      # prune
            Fact.make("old2", last_accessed=OLD, access_count=0, mutability="refinable", digest="d"),          # prune+degrade
            Fact.make("pin", last_accessed=OLD, access_count=0, mutability="refinable", digest="d", pinned=True),   # 排除
            Fact.make("canon", last_accessed=OLD, access_count=0, mutability="canonical", digest="d"),         # 排除(canonical)
            Fact.make("fresh", last_accessed=NOW, access_count=5, mutability="refinable"),                     # 活跃
            Fact.make("sup", last_accessed=OLD, access_count=0, mutability="refinable", superseded_by="x"),    # superseded
        ]

    def test_counts(self):
        s = aggregate_memory_signals(self._facts(), NOW, max_age_days=90, min_access_count=1)
        self.assertEqual(s["total"], 6)
        self.assertEqual(s["active"], 5)
        self.assertEqual(s["superseded"], 1)
        self.assertEqual(s["canonical"], 1)
        self.assertEqual(s["refinable_active"], 4)    # old1/old2/pin/fresh (sup superseded, canon 非refinable)
        # prune_stale 真实谓词不排除 superseded → sup(老+低访问) 也会被删, 故 3 (与算子一致)
        self.assertEqual(s["prune_candidates"], 3)    # old1/old2/sup (pin/canon/fresh 排除)
        self.assertEqual(s["degrade_candidates"], 1)  # old2 (有 digest; sup 无 digest)

    def test_empty(self):
        s = aggregate_memory_signals([], NOW)
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["prune_candidates"], 0)


class TestProposeMemory(unittest.TestCase):
    def test_all_triggers(self):
        props = propose_from_memory_signals(
            {"prune_candidates": 2, "degrade_candidates": 1, "refinable_active": 6, "max_age_days": 90})
        self.assertEqual([p.action_type for p in props],
                         ["run_prune", "run_degrade", "run_reconcile", "run_consolidate"])

    def test_reconcile_at_2_no_consolidate(self):
        props = propose_from_memory_signals(
            {"prune_candidates": 0, "degrade_candidates": 0, "refinable_active": 2})
        self.assertEqual([p.action_type for p in props], ["run_reconcile"])

    def test_nothing_when_below_thresholds(self):
        props = propose_from_memory_signals(
            {"prune_candidates": 0, "degrade_candidates": 0, "refinable_active": 1})
        self.assertEqual(props, [])


class TestAgentMemoryMaintenance(unittest.TestCase):
    def _agent(self, enable=True, memory=True):
        ltm = LongTermMemoryImpl(backend=InMemoryBackend()) if memory else None
        a = SimpleAgent(tool_registry=_Reg(), llm_planner=None, long_term_memory=ltm)
        a.enable_self_reflection = enable
        return a

    def test_disabled_gate(self):
        self.assertFalse(self._agent(enable=False).propose_memory_maintenance()["enabled"])

    def test_no_memory_fail_safe(self):
        r = self._agent(memory=False).propose_memory_maintenance()
        self.assertTrue(r["enabled"])
        self.assertEqual(r["proposals"], [])
        self.assertIn("未接", r["note"])

    def test_propose_then_apply_prune_deletes_stale(self):
        a = self._agent()
        a.long_term_memory.add_fact(Fact.make("stale1", tenant_id="t1", last_accessed=OLD, access_count=0))
        a.long_term_memory.add_fact(Fact.make("stale2", tenant_id="t1", last_accessed=OLD, access_count=0))
        a.long_term_memory.add_fact(Fact.make("fresh", tenant_id="t1", last_accessed=time.time(), access_count=3))
        r = a.propose_memory_maintenance(tenant_id="t1")
        self.assertTrue(r["enabled"])
        self.assertEqual(r["signals"]["prune_candidates"], 2)
        prune_props = [p for p in r["proposals"] if p["action_type"] == "run_prune"]
        self.assertEqual(len(prune_props), 1)
        # 仅审批 run_prune → 真删 2 条陈旧
        res = a.apply_memory_maintenance(r["proposals"], approved_ids=[prune_props[0]["id"]], tenant_id="t1")
        self.assertEqual(res["applied"], 1)
        remaining = a.long_term_memory.list_facts("t1")
        self.assertEqual(len(remaining), 1)            # 只剩 fresh
        self.assertEqual(remaining[0].content, "fresh")

    def test_apply_nothing_approved_no_op(self):
        a = self._agent()
        a.long_term_memory.add_fact(Fact.make("stale1", tenant_id="t1", last_accessed=OLD, access_count=0))
        r = a.propose_memory_maintenance(tenant_id="t1")
        res = a.apply_memory_maintenance(r["proposals"], approved_ids=[], tenant_id="t1")
        self.assertEqual(res["applied"], 0)
        self.assertEqual(len(a.long_term_memory.list_facts("t1")), 1)   # 未删


if __name__ == "__main__":
    unittest.main()
