# -*- coding: utf-8 -*-
"""执行计划⑦ 统一配置界面后端: /config/agent (读/写) + /config/agent/preset(s)。"""
import os
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import unittest
from fastapi.testclient import TestClient

from api_service_module.core.impl import ApiService
from agent_module.core.impl import SimpleAgent


class _MockHandler:
    def handle(self, request, trace_id=None):
        return {"code": "SUCCESS", "message": "ok", "data": {}, "trace_id": trace_id,
                "retryable": False, "details": None}


class _Reg:
    def __init__(self): self._t = {}
    def register(self, n, f): self._t[n] = f
    def unregister(self, n): return self._t.pop(n, None) is not None
    def get(self, n): return self._t.get(n)
    def list_tools(self): return list(self._t.keys())


def _svc(with_agent=True):
    agent = SimpleAgent(tool_registry=_Reg(), llm_planner=None) if with_agent else None
    svc = ApiService(handler=_MockHandler(), agent=agent)
    svc.auth_enabled = False
    return svc, agent


class TestConfigAgentRoutes(unittest.TestCase):
    def test_get_dump(self):
        svc, _ = _svc()
        r = TestClient(svc.app).get("/config/agent")
        self.assertEqual(r.status_code, 200)
        fields = r.json()["data"]["fields"]
        self.assertTrue(fields)
        keys = {f["key"] for f in fields}
        self.assertIn("agent.execution_strategy", keys)
        self.assertIn("agent.model_routing_enabled", keys)

    def test_post_updates_live(self):
        svc, agent = _svc()
        r = TestClient(svc.app).post("/config/agent",
                                     json={"updates": {"agent.execution_strategy": "single_shot",
                                                       "agent.enable_query_refine": True}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(agent.execution_strategy, "single_shot")   # live 生效
        self.assertIs(agent.enable_query_refine, True)
        self.assertIn("agent.execution_strategy", r.json()["data"]["applied"])

    def test_get_presets(self):
        svc, _ = _svc()
        r = TestClient(svc.app).get("/config/agent/presets")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json()["data"]["presets"]), {"conservative", "balanced", "aggressive"})

    def test_apply_preset_live(self):
        svc, agent = _svc()
        r = TestClient(svc.app).post("/config/agent/preset", json={"name": "aggressive"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(agent.enable_self_reflection)       # 进取档开自维护
        self.assertEqual(agent.query_refine_mode, "ask")

    def test_no_agent_501(self):
        svc, _ = _svc(with_agent=False)
        client = TestClient(svc.app)
        self.assertEqual(client.get("/config/agent").status_code, 501)
        self.assertEqual(client.post("/config/agent", json={}).status_code, 501)


if __name__ == "__main__":
    unittest.main()
