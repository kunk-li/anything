# -*- coding: utf-8 -*-
"""
/v1/ 镜像路由单测 (Task VV #82).

验证:
    - 老 path 仍工作 (向后兼容)
    - /v1/<path> 别名指向同一个 endpoint, 行为等价
    - 排除清单中的 path (/, /ui, /health, /healthz) 不会被加 /v1 前缀
"""
from __future__ import annotations

import os
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import unittest
from fastapi.testclient import TestClient

from api_service_module.core.impl import ApiService


class _MockHandler:
    def handle(self, request, trace_id=None):
        rtype = request.get("type", "rag")
        if rtype == "rag" and not request.get("query"):
            return {
                "code": "PARAM_MISSING", "message": "缺少 query", "data": None,
                "trace_id": trace_id, "retryable": False, "details": {"field": "query"},
            }
        return {
            "code": "SUCCESS", "message": "ok", "data": {"echo": request},
            "trace_id": trace_id, "retryable": False, "details": None,
        }


class TestV1Aliases(unittest.TestCase):
    def setUp(self):
        self.service = ApiService(handler=_MockHandler())
        self.service.auth_enabled = False
        self.client = TestClient(self.service.app)

    def test_old_path_invoke_still_works(self):
        """老 path /invoke 仍工作 (向后兼容)."""
        r = self.client.post("/invoke", json={"type": "rag", "query": "test"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["code"], "SUCCESS")

    def test_v1_invoke_alias_works(self):
        """新 path /v1/invoke 指向同一 endpoint, 等价响应."""
        r = self.client.post("/v1/invoke", json={"type": "rag", "query": "test"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["code"], "SUCCESS")
        self.assertEqual(r.json()["data"]["echo"]["query"], "test")

    def test_v1_invoke_validation_same_as_old(self):
        """v1 路径校验逻辑跟老 path 一致."""
        r = self.client.post("/v1/invoke", json={"type": "rag"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "PARAM_MISSING")

    def test_v1_metrics_alias(self):
        r = self.client.get("/v1/metrics")
        self.assertEqual(r.status_code, 200)
        # /metrics 是 Prometheus text format (text/plain)
        # 老 path 行为
        r_old = self.client.get("/metrics")
        self.assertEqual(r.status_code, r_old.status_code)

    def test_v1_admin_status_alias(self):
        r = self.client.get("/v1/admin/status")
        self.assertEqual(r.status_code, 200)

    def test_v1_config_models_alias(self):
        r = self.client.get("/v1/config/models")
        # /config/models 在没 llm_service 时返回 SERVICE_UNAVAILABLE 501
        self.assertIn(r.status_code, (200, 501))

    def test_v1_documents_list_alias(self):
        r = self.client.get("/v1/documents")
        # 没有 document_store_factory 时可能 500/501, 关键是 path 能命中
        self.assertNotEqual(r.status_code, 404,
                            "/v1/documents alias not registered")

    def test_health_path_not_aliased(self):
        """k8s liveness probe 习惯打 /health, 排除清单不加 v1 前缀."""
        r_old = self.client.get("/health")
        self.assertEqual(r_old.status_code, 200)
        # /v1/health 不应该存在
        r_v1 = self.client.get("/v1/health")
        self.assertEqual(r_v1.status_code, 404)

    def test_healthz_path_not_aliased(self):
        r_v1 = self.client.get("/v1/healthz")
        self.assertEqual(r_v1.status_code, 404)

    def test_root_path_not_aliased(self):
        """SPA 入口 / 不带 v1 前缀."""
        r_v1 = self.client.get("/v1/")
        self.assertEqual(r_v1.status_code, 404)

    def test_v1_aliases_count_in_app_routes(self):
        """sanity: app.routes 包含 /v1/* path 路由."""
        paths = [getattr(r, "path", None) for r in self.service.app.routes]
        v1_paths = [p for p in paths if p and p.startswith("/v1/")]
        # 至少应该有 /v1/invoke + /v1/metrics + /v1/admin/status + /v1/config/models +
        # /v1/documents + /v1/index/build + /v1/index/job/{job_id} + ws + ...
        self.assertGreater(len(v1_paths), 8,
                           f"期望至少 8+ 条 v1 路由, 实际只有 {len(v1_paths)}: {v1_paths}")


if __name__ == "__main__":
    unittest.main()
