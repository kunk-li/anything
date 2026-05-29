# -*- coding: utf-8 -*-
"""
/memory/* 路由测试 (Task GGG #93).

覆盖:
    GET    /memory/list
    GET    /memory/{fact_id}
    DELETE /memory/{fact_id}
    POST   /memory/{fact_id}/pin
    POST   /memory/search
    long_term_memory=None 时各路由返 501
    /v1/memory/* 镜像 (VV #82 自动加)
"""
import os
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import unittest
from fastapi.testclient import TestClient

from api_service_module.core.impl import ApiService
from long_term_memory_module import LongTermMemoryImpl, Fact
from state_backend_module import InMemoryBackend


class _MockHandler:
    def handle(self, request, trace_id=None):
        return {
            "code": "SUCCESS", "message": "ok",
            "data": {"echo": request}, "trace_id": trace_id,
            "retryable": False, "details": None,
        }


def _make_service_with_memory():
    memory = LongTermMemoryImpl(backend=InMemoryBackend())
    svc = ApiService(handler=_MockHandler(), long_term_memory=memory)
    svc.auth_enabled = False
    return svc, memory


def _make_service_without_memory():
    svc = ApiService(handler=_MockHandler(), long_term_memory=None)
    svc.auth_enabled = False
    return svc


class TestMemoryListRoute(unittest.TestCase):

    def test_empty_list(self):
        svc, _ = _make_service_with_memory()
        client = TestClient(svc.app)
        r = client.get("/memory/list?tenant_id=t1")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["code"], "SUCCESS")
        self.assertEqual(body["data"]["count"], 0)
        self.assertEqual(body["data"]["facts"], [])

    def test_list_returns_added_facts(self):
        svc, memory = _make_service_with_memory()
        memory.add_fact(Fact.make("user likes Python", tenant_id="t1", tags=["preference"]))
        memory.add_fact(Fact.make("user works at Anthropic", tenant_id="t1", tags=["context"]))
        client = TestClient(svc.app)
        r = client.get("/memory/list?tenant_id=t1")
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        self.assertEqual(data["count"], 2)
        contents = {x["content"] for x in data["facts"]}
        self.assertEqual(contents, {"user likes Python", "user works at Anthropic"})

    def test_list_tag_filter(self):
        svc, memory = _make_service_with_memory()
        memory.add_fact(Fact.make("pref_fact", tenant_id="t1", tags=["preference"]))
        memory.add_fact(Fact.make("dec_fact", tenant_id="t1", tags=["decision"]))
        client = TestClient(svc.app)
        r = client.get("/memory/list?tenant_id=t1&tags=preference")
        self.assertEqual(r.status_code, 200)
        contents = {x["content"] for x in r.json()["data"]["facts"]}
        self.assertEqual(contents, {"pref_fact"})

    def test_list_pagination(self):
        svc, memory = _make_service_with_memory()
        for i in range(5):
            memory.add_fact(Fact.make(f"fact_{i}", tenant_id="t1"))
        client = TestClient(svc.app)
        r = client.get("/memory/list?tenant_id=t1&limit=2&offset=0")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["data"]["facts"]), 2)

    def test_unavailable_when_no_memory(self):
        svc = _make_service_without_memory()
        client = TestClient(svc.app)
        r = client.get("/memory/list")
        self.assertEqual(r.status_code, 501)
        self.assertEqual(r.json()["code"], "SERVICE_UNAVAILABLE")


class TestMemoryGetRoute(unittest.TestCase):

    def test_get_returns_full_fact(self):
        svc, memory = _make_service_with_memory()
        f = memory.add_fact(Fact.make("test_get", tenant_id="t1", tags=["fact"]))
        client = TestClient(svc.app)
        r = client.get(f"/memory/{f.fact_id}?tenant_id=t1")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["data"]["fact_id"], f.fact_id)
        self.assertEqual(body["data"]["content"], "test_get")

    def test_get_unknown_returns_404(self):
        svc, _ = _make_service_with_memory()
        client = TestClient(svc.app)
        r = client.get("/memory/nonexistent_id?tenant_id=t1")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "MEMORY_NOT_FOUND")


class TestMemoryDeleteRoute(unittest.TestCase):

    def test_delete_works(self):
        svc, memory = _make_service_with_memory()
        f = memory.add_fact(Fact.make("delete_me", tenant_id="t1"))
        client = TestClient(svc.app)
        r = client.delete(f"/memory/{f.fact_id}?tenant_id=t1")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["data"]["deleted"])
        # 再查应 404
        r2 = client.get(f"/memory/{f.fact_id}?tenant_id=t1")
        self.assertEqual(r2.status_code, 404)

    def test_delete_unknown_returns_404(self):
        svc, _ = _make_service_with_memory()
        client = TestClient(svc.app)
        r = client.delete("/memory/nonexistent_id?tenant_id=t1")
        self.assertEqual(r.status_code, 404)


class TestMemoryPinRoute(unittest.TestCase):

    def test_pin_sets_flag(self):
        svc, memory = _make_service_with_memory()
        f = memory.add_fact(Fact.make("important", tenant_id="t1"))
        client = TestClient(svc.app)
        r = client.post(f"/memory/{f.fact_id}/pin?tenant_id=t1",
                       json={"pinned": True})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["data"]["pinned"])
        # 后端实际值变了
        reloaded = memory._load_fact("t1", f.fact_id)
        self.assertTrue(reloaded.pinned)

    def test_unpin(self):
        svc, memory = _make_service_with_memory()
        f = memory.add_fact(Fact.make("x", tenant_id="t1", pinned=True))
        client = TestClient(svc.app)
        r = client.post(f"/memory/{f.fact_id}/pin?tenant_id=t1",
                       json={"pinned": False})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["data"]["pinned"])

    def test_pin_unknown_returns_404(self):
        svc, _ = _make_service_with_memory()
        client = TestClient(svc.app)
        r = client.post("/memory/nonexistent/pin?tenant_id=t1",
                       json={"pinned": True})
        self.assertEqual(r.status_code, 404)


class TestMemorySearchRoute(unittest.TestCase):

    def test_search_returns_hits(self):
        svc, memory = _make_service_with_memory()
        memory.add_fact(Fact.make("user prefers Python", tenant_id="t1"))
        client = TestClient(svc.app)
        r = client.post("/memory/search?tenant_id=t1", json={"query": "Python"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["code"], "SUCCESS")
        self.assertGreater(body["data"]["count"], 0)
        self.assertEqual(body["data"]["query"], "Python")

    def test_search_missing_query(self):
        svc, _ = _make_service_with_memory()
        client = TestClient(svc.app)
        r = client.post("/memory/search?tenant_id=t1", json={})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "PARAM_MISSING")

    def test_search_bad_body(self):
        svc, _ = _make_service_with_memory()
        client = TestClient(svc.app)
        r = client.post("/memory/search?tenant_id=t1", content=b"not json",
                       headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "BAD_REQUEST")


class TestV1MemoryAliases(unittest.TestCase):
    """VV #82 已经把所有 API 加 /v1/ 镜像, 验证 /v1/memory/* 也工作."""

    def test_v1_memory_list_alias(self):
        svc, memory = _make_service_with_memory()
        memory.add_fact(Fact.make("x", tenant_id="t1"))
        client = TestClient(svc.app)
        r_old = client.get("/memory/list?tenant_id=t1")
        r_v1 = client.get("/v1/memory/list?tenant_id=t1")
        self.assertEqual(r_old.status_code, 200)
        self.assertEqual(r_v1.status_code, 200)
        # 内容等价 (count 一致)
        self.assertEqual(
            r_old.json()["data"]["count"],
            r_v1.json()["data"]["count"],
        )

    def test_v1_memory_search_alias(self):
        svc, memory = _make_service_with_memory()
        memory.add_fact(Fact.make("hello world", tenant_id="t1"))
        client = TestClient(svc.app)
        r = client.post("/v1/memory/search?tenant_id=t1",
                       json={"query": "hello"})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
