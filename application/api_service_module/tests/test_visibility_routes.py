# -*- coding: utf-8 -*-
"""执行计划⑥ 可见性面板后端: /memory/profile + /agent/maintenance/proposals + /apply。"""
import os
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import unittest
from fastapi.testclient import TestClient

from api_service_module.core.impl import ApiService
from long_term_memory_module import LongTermMemoryImpl, Fact
from state_backend_module import InMemoryBackend


class _MockHandler:
    def handle(self, request, trace_id=None):
        return {"code": "SUCCESS", "message": "ok", "data": {"echo": request},
                "trace_id": trace_id, "retryable": False, "details": None}


class _FakeAgent:
    """fake: 维护提议/审批 (run_maintenance_scan / apply_memory_maintenance)。"""
    def run_maintenance_scan(self, tenant_id="default", trace_id=None, scope=(), **kw):
        return {"enabled": True, "tenant_id": tenant_id, "total_proposals": 1,
                "by_domain": {"memory": 1},
                "proposals": [{"id": "p1", "action_type": "run_prune", "domain": "memory",
                               "reason": "3 条陈旧 fact"}]}
    def apply_memory_maintenance(self, proposals, approved_ids, tenant_id="default", trace_id=None):
        return {"applied": len(approved_ids),
                "details": [{"id": i, "op": "run_prune", "result": 3} for i in approved_ids]}


class TestMemoryProfileRoute(unittest.TestCase):
    def _svc(self, with_memory=True):
        mem = LongTermMemoryImpl(backend=InMemoryBackend()) if with_memory else None
        svc = ApiService(handler=_MockHandler(), long_term_memory=mem)
        svc.auth_enabled = False
        return svc, mem

    def test_profile_returns_dims(self):
        svc, mem = self._svc()
        mem.add_fact(Fact.make("偏好 Python + 类型标注", tenant_id="t1", content_type="preference"))
        mem.add_fact(Fact.make("Python 后端开发者", tenant_id="t1", content_type="domain"))
        r = TestClient(svc.app).get("/memory/profile?tenant_id=t1")
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        self.assertGreaterEqual(data["total"], 2)
        self.assertIn("preference", data["profile"])
        self.assertIn("domain", data["profile"])

    def test_profile_empty(self):
        svc, _ = self._svc()
        r = TestClient(svc.app).get("/memory/profile?tenant_id=t1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["total"], 0)

    def test_profile_no_memory_501(self):
        svc, _ = self._svc(with_memory=False)
        r = TestClient(svc.app).get("/memory/profile?tenant_id=t1")
        self.assertEqual(r.status_code, 501)


class TestMaintenanceRoutes(unittest.TestCase):
    def _svc(self, agent=True):
        svc = ApiService(handler=_MockHandler(),
                         long_term_memory=LongTermMemoryImpl(backend=InMemoryBackend()),
                         agent=_FakeAgent() if agent else None)
        svc.auth_enabled = False
        return svc

    def test_proposals(self):
        r = TestClient(self._svc().app).get("/agent/maintenance/proposals?tenant_id=t1&scope=memory")
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        self.assertTrue(data["enabled"])
        self.assertEqual(data["total_proposals"], 1)
        self.assertEqual(data["proposals"][0]["id"], "p1")

    def test_apply(self):
        body = {"proposals": [{"id": "p1", "action_type": "run_prune"}], "approved_ids": ["p1"]}
        r = TestClient(self._svc().app).post("/agent/maintenance/apply", json=body)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["applied"], 1)

    def test_proposals_no_agent_501(self):
        r = TestClient(self._svc(agent=False).app).get("/agent/maintenance/proposals")
        self.assertEqual(r.status_code, 501)

    def test_apply_no_agent_501(self):
        r = TestClient(self._svc(agent=False).app).post("/agent/maintenance/apply", json={})
        self.assertEqual(r.status_code, 501)


if __name__ == "__main__":
    unittest.main()
