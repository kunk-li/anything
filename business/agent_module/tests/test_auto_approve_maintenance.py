# -*- coding: utf-8 -*-
"""方向4 更高自主档: standing-approval 预授权策略 — 人预授权的安全维护 action 才自动执行,
默认名单空=零自动=完全 human-in-loop; 安全天花板只放 run_prune/run_degrade。
"""
import time
import unittest

from agent_module.core.impl import SimpleAgent
from state_backend_module import InMemoryBackend
from long_term_memory_module.core.impl import LongTermMemoryImpl
from long_term_memory_module.model import Fact

OLD = time.time() - 100 * 86400


class _Reg:
    def __init__(self): self._t = {}
    def register(self, n, f): self._t[n] = f
    def unregister(self, n): return self._t.pop(n, None) is not None
    def get(self, n): return self._t.get(n)
    def list_tools(self): return list(self._t.keys())


class TestAutoApproveMaintenance(unittest.TestCase):
    def _agent(self, policy=None, enable=True):
        a = SimpleAgent(tool_registry=_Reg(), llm_planner=None,
                        long_term_memory=LongTermMemoryImpl(backend=InMemoryBackend()))
        a.enable_self_reflection = enable
        if policy is not None:
            a.auto_approve_maintenance = set(policy)
        return a

    def test_default_policy_empty(self):
        self.assertEqual(self._agent().auto_approve_maintenance, set())

    def test_empty_policy_no_autoapply_even_if_forced(self):
        a = self._agent()                       # 空名单
        a.long_term_memory.add_fact(Fact.make("stale", tenant_id="t1", last_accessed=OLD, access_count=0))
        r = a.run_maintenance_scan(tenant_id="t1", scope=("memory",), auto_apply=True)
        self.assertNotIn("auto_applied", r)     # 名单空 → 不自动执行
        self.assertEqual(len(a.long_term_memory.list_facts("t1")), 1)   # 未删

    def test_preauthorized_prune_autoapplies(self):
        a = self._agent(policy={"run_prune"})
        a.long_term_memory.add_fact(Fact.make("stale", tenant_id="t1", last_accessed=OLD, access_count=0))
        a.long_term_memory.add_fact(Fact.make("fresh", tenant_id="t1", last_accessed=time.time(), access_count=3))
        r = a.run_maintenance_scan(tenant_id="t1", scope=("memory",))   # auto_apply=None → 按名单
        self.assertIn("auto_applied", r)
        self.assertGreaterEqual(r["auto_applied"]["applied"], 1)
        remaining = a.long_term_memory.list_facts("t1")
        self.assertEqual(len(remaining), 1)     # 陈旧被自动 prune, 只剩 fresh
        self.assertEqual(remaining[0].content, "fresh")

    def test_ineligible_action_not_autoapplied(self):
        # 名单含 run_reconcile (LLM 类, 不在安全天花板) → 即便 auto_apply 也不执行
        a = self._agent(policy={"run_reconcile"})
        a.long_term_memory.add_fact(Fact.make("stale", tenant_id="t1", last_accessed=OLD, access_count=0))
        r = a.run_maintenance_scan(tenant_id="t1", scope=("memory",), auto_apply=True)
        self.assertNotIn("auto_applied", r)     # run_reconcile ∉ {run_prune,run_degrade}
        self.assertEqual(len(a.long_term_memory.list_facts("t1")), 1)

    def test_auto_apply_false_forces_propose_only(self):
        a = self._agent(policy={"run_prune"})   # 有名单, 但显式 auto_apply=False
        a.long_term_memory.add_fact(Fact.make("stale", tenant_id="t1", last_accessed=OLD, access_count=0))
        r = a.run_maintenance_scan(tenant_id="t1", scope=("memory",), auto_apply=False)
        self.assertNotIn("auto_applied", r)     # 强制关 → 纯提议
        self.assertEqual(len(a.long_term_memory.list_facts("t1")), 1)


if __name__ == "__main__":
    unittest.main()
