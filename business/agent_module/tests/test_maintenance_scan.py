# -*- coding: utf-8 -*-
"""方向4 定时提议+通知: run_maintenance_scan 聚合各自维护域提议 + 审计通知 (不自动 apply);
可由 TaskScheduler 经 extra_params.maintenance_scan=true 的请求周期触发。
"""
import os
import tempfile
import time
import unittest

from agent_module.core.impl import SimpleAgent
from state_backend_module import InMemoryBackend
from long_term_memory_module.core.impl import LongTermMemoryImpl
from long_term_memory_module.model import Fact

OLD = time.time() - 100 * 86400      # 100 天前 (> 90, 触发 prune 提议)


class _Reg:
    def __init__(self): self._t = {}
    def register(self, n, f): self._t[n] = f
    def unregister(self, n): return self._t.pop(n, None) is not None
    def get(self, n): return self._t.get(n)
    def list_tools(self): return list(self._t.keys())


class TestRunMaintenanceScan(unittest.TestCase):
    def _agent(self, enable=True):
        a = SimpleAgent(tool_registry=_Reg(), llm_planner=None,
                        long_term_memory=LongTermMemoryImpl(backend=InMemoryBackend()))
        a.enable_self_reflection = enable
        return a

    def test_disabled_gate(self):
        self.assertFalse(self._agent(enable=False).run_maintenance_scan()["enabled"])

    def test_aggregates_memory_and_code_doc(self):
        a = self._agent()
        a.long_term_memory.add_fact(Fact.make("stale", tenant_id="t1", last_accessed=OLD, access_count=0))
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "business", "x_module"))   # 缺 README
            r = a.run_maintenance_scan(tenant_id="t1", root=root, scope=("memory", "code_doc"))
        self.assertTrue(r["enabled"])
        self.assertGreaterEqual(r["by_domain"]["memory"], 1)          # prune 提议
        self.assertGreaterEqual(r["by_domain"]["code_doc"], 1)        # 缺 README 提议
        self.assertEqual(r["total_proposals"], sum(r["by_domain"].values()))
        self.assertTrue(all("domain" in p for p in r["proposals"]))   # 每条带域标记

    def test_domain_failure_isolated(self):
        # 单域异常不影响整体 (fail-safe): code_doc 给个不存在的 root → 该域空, 其余正常
        a = self._agent()
        a.long_term_memory.add_fact(Fact.make("stale", tenant_id="t1", last_accessed=OLD, access_count=0))
        r = a.run_maintenance_scan(tenant_id="t1", root="/no/such/dir/xyz", scope=("memory", "code_doc"))
        self.assertTrue(r["enabled"])
        self.assertGreaterEqual(r["by_domain"]["memory"], 1)
        self.assertEqual(r["by_domain"]["code_doc"], 0)               # 坏 root → 空, 不崩

    def test_execute_hook_routes_to_scan(self):
        a = self._agent()
        resp = a.execute({"task": "x", "trace_id": "t", "session_id": "s",
                          "extra_params": {"maintenance_scan": True, "maintenance_scope": ["memory"]}})
        self.assertEqual(resp["code"], "SUCCESS")
        self.assertEqual(resp["message"], "maintenance_scan")
        self.assertTrue(resp["data"]["enabled"])
        self.assertIn("memory", resp["data"]["by_domain"])


if __name__ == "__main__":
    unittest.main()
