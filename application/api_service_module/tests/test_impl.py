# -*- coding: utf-8 -*-
"""
ApiService 单元测试

覆盖:
    - /invoke 主入口路由(成功 / 校验失败)
    - /health 健康检查
    - /metrics Prometheus 文本端点 + 计数器累积
    - 异常响应统一信封格式
"""

from __future__ import annotations

import os

# 单独跑本测试时(unittest discover)显式启用 dev mode 避免 build_basic_deps
# 在生产模式因 secrets 未配抛 StartupError. 通过 scripts/run_tests.sh 跑时
# 该变量已被设置, 这里 setdefault 不覆盖.
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import unittest

from fastapi.testclient import TestClient

from api_service_module.core.impl import ApiService


class MockHandler:
    """模拟接口层 handler. 注意当前 ApiService 不再接收 config 参数,
    所有配置走 ConfigManager + deps 注入(详见 Task #6 / #29)."""

    def __init__(self):
        self.calls = []

    def handle(self, request, trace_id=None):
        self.calls.append({"request": request, "trace_id": trace_id})
        # 缺 query / task 时返回 PARAM_MISSING 模拟 RequestHandler 行为
        rtype = request.get("type", "rag")
        if rtype == "rag" and not request.get("query"):
            return {
                "code": "PARAM_MISSING",
                "message": "缺少 query",
                "data": None,
                "trace_id": trace_id,
                "retryable": False,
                "details": {"field": "query"},
            }
        return {
            "code": "SUCCESS",
            "message": "ok",
            "data": {"echo": request},
            "trace_id": trace_id,
            "retryable": False,
            "details": None,
        }


class TestApiService(unittest.TestCase):

    def setUp(self):
        self.handler = MockHandler()
        self.service = ApiService(handler=self.handler)
        # 关闭鉴权,让测试用例可以直接调用 /invoke 不带 API Key
        # (生产配置 security.auth_enabled=true 默认开启)
        self.service.auth_enabled = False
        self.client = TestClient(self.service.app)

    def test_invoke_success(self):
        response = self.client.post("/invoke", json={"type": "rag", "query": "test", "top_k": 3})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], "SUCCESS")
        self.assertEqual(payload["data"]["echo"]["query"], "test")
        # trace_id 必须存在
        self.assertIn("trace_id", payload)
        # X-Request-Id header 必须存在
        self.assertIn("x-request-id", {k.lower() for k in response.headers.keys()})

    def test_invoke_missing_query_returns_param_missing(self):
        response = self.client.post("/invoke", json={"type": "rag"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "PARAM_MISSING")

    def test_invoke_invalid_json(self):
        response = self.client.post("/invoke", content=b"not json", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "BAD_REQUEST")

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], "SUCCESS")

    def test_healthz_endpoint(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)


class TestTenantBinding(unittest.TestCase):
    """Task #33 PR2: api_keys 反向映射 + 冲突处理"""

    def test_legacy_list_format_maps_all_to_default(self):
        """老 list 格式: 全部 key 映射到 default + 启动 WARN"""
        service = ApiService(handler=MockHandler())
        service.api_keys = []
        idx = service._build_key_to_tenant_index(["k1", "k2", "k3"])
        self.assertEqual(idx, {"k1": "default", "k2": "default", "k3": "default"})

    def test_new_dict_format_parses(self):
        """新 dict 格式: tenant -> keys list 反向解析"""
        service = ApiService(handler=MockHandler())
        idx = service._build_key_to_tenant_index({
            "tenant-a": ["key_a1", "key_a2"],
            "default": ["key_internal"],
        })
        self.assertEqual(idx["key_a1"], "tenant-a")
        self.assertEqual(idx["key_a2"], "tenant-a")
        self.assertEqual(idx["key_internal"], "default")

    def test_dict_format_one_key_multi_tenant_raises_startup_error(self):
        """决议 3: 一个 key 绑多 tenant -> StartupError fail-fast"""
        from deps_module import StartupError
        service = ApiService(handler=MockHandler())
        with self.assertRaises(StartupError) as ctx:
            service._build_key_to_tenant_index({
                "tenant-a": ["shared_key"],
                "tenant-b": ["shared_key"],  # 同一 key 绑两个 tenant
            })
        self.assertIn("multiple tenants", str(ctx.exception))

    def test_dict_format_keys_not_list_raises(self):
        from deps_module import StartupError
        service = ApiService(handler=MockHandler())
        with self.assertRaises(StartupError):
            service._build_key_to_tenant_index({"tenant-a": "not_a_list"})

    def test_resolve_tenant_from_apikey_header(self):
        """API key header 应映射到对应 tenant_id"""
        from unittest.mock import MagicMock
        service = ApiService(handler=MockHandler())
        service.auth_enabled = True
        service.auth_type = "apikey"
        service._key_to_tenant = {"key_a1": "tenant-a", "key_internal": "default"}

        req_a = MagicMock()
        req_a.headers = {"X-API-Key": "key_a1"}
        self.assertEqual(service._resolve_tenant_from_auth(req_a), "tenant-a")

        req_unknown = MagicMock()
        req_unknown.headers = {"X-API-Key": "unknown_key"}
        self.assertIsNone(service._resolve_tenant_from_auth(req_unknown))

        req_no_header = MagicMock()
        req_no_header.headers = {}
        self.assertIsNone(service._resolve_tenant_from_auth(req_no_header))

    def test_auth_disabled_resolve_returns_none(self):
        """auth_enabled=False 时不解析 tenant_id"""
        from unittest.mock import MagicMock
        service = ApiService(handler=MockHandler())
        service.auth_enabled = False
        req = MagicMock()
        req.headers = {"X-API-Key": "key_a1"}
        self.assertIsNone(service._resolve_tenant_from_auth(req))

    def test_reconcile_auth_wins_over_body(self):
        """冲突: auth=tenant-a, body=tenant-b -> 强制 auth + ERROR 日志"""
        from unittest.mock import MagicMock
        service = ApiService(handler=MockHandler())
        service.auth_enabled = True
        service.auth_type = "apikey"
        service._key_to_tenant = {"key_a1": "tenant-a"}

        req = MagicMock()
        req.headers = {"X-API-Key": "key_a1"}
        req.client = MagicMock(host="203.0.113.1")  # 非 internal IP

        body = {"type": "rag", "query": "x", "tenant_id": "tenant-b"}
        service._reconcile_tenant_id(req, body, trace_id="t1")
        # auth 赢: body 被改写
        self.assertEqual(body["tenant_id"], "tenant-a")

    def test_reconcile_body_only_external_ip_ignored(self):
        """无认证 + 非 internal IP + body 声明 tenant -> 忽略 body 声明"""
        from unittest.mock import MagicMock
        service = ApiService(handler=MockHandler())
        service.auth_enabled = False  # 无认证
        service.internal_whitelist = []

        req = MagicMock()
        req.headers = {}
        req.client = MagicMock(host="203.0.113.1")

        body = {"type": "rag", "query": "x", "tenant_id": "tenant-attacker"}
        service._reconcile_tenant_id(req, body, trace_id="t1")
        # body 的 tenant_id 被剥除, 后续 RequestHandler 会补 default
        self.assertNotIn("tenant_id", body)

    def test_reconcile_body_only_internal_ip_preserved(self):
        """无认证 + internal IP + body 声明 -> 保留 body"""
        from unittest.mock import MagicMock
        service = ApiService(handler=MockHandler())
        service.auth_enabled = False
        service.internal_whitelist = ["127.0.0.1", "10.0.0."]

        req = MagicMock()
        req.headers = {}
        req.client = MagicMock(host="127.0.0.1")

        body = {"type": "rag", "query": "x", "tenant_id": "tenant-internal"}
        service._reconcile_tenant_id(req, body, trace_id="t1")
        self.assertEqual(body.get("tenant_id"), "tenant-internal")


class TestMetricsEndpoint(unittest.TestCase):
    """专门覆盖 /metrics — Task #27 新增"""

    def setUp(self):
        self.handler = MockHandler()
        self.service = ApiService(handler=self.handler)
        # 关闭鉴权,让测试用例可以直接调用 /invoke 不带 API Key
        # (生产配置 security.auth_enabled=true 默认开启)
        self.service.auth_enabled = False
        self.client = TestClient(self.service.app)

    def test_metrics_initially_empty(self):
        """没请求过任何东西时 /metrics 只输出 HELP/TYPE 头部"""
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        text = response.text
        # 应该有 4 类 metrics 的 HELP 描述, 但没有具体数值行
        self.assertIn("# HELP anything_requests_total", text)
        self.assertIn("# TYPE anything_requests_total counter", text)
        # 检查 content-type
        self.assertIn("text/plain", response.headers["content-type"])

    def test_metrics_after_successful_invoke(self):
        # 触发一次成功 RAG 请求
        self.client.post("/invoke", json={"type": "rag", "query": "x"})
        response = self.client.get("/metrics")
        text = response.text
        # 应包含 type="rag" 计数 = 1
        self.assertIn('anything_requests_total{type="rag"} 1', text)
        # duration_count_by_type 也应该 = 1
        self.assertIn('anything_request_duration_seconds_count{type="rag"} 1', text)
        # SUCCESS 不应进 errors_total
        self.assertNotIn('anything_errors_total{code=', text)

    def test_metrics_after_error_invoke(self):
        # 触发一次 PARAM_MISSING 请求
        self.client.post("/invoke", json={"type": "rag"})
        response = self.client.get("/metrics")
        text = response.text
        self.assertIn('anything_requests_total{type="rag"} 1', text)
        self.assertIn('anything_errors_total{code="PARAM_MISSING"} 1', text)

    def test_metrics_accumulates_across_requests(self):
        for _ in range(3):
            self.client.post("/invoke", json={"type": "rag", "query": "x"})
        for _ in range(2):
            self.client.post("/invoke", json={"type": "agent", "task": "y"})
        text = self.client.get("/metrics").text
        self.assertIn('anything_requests_total{type="rag"} 3', text)
        self.assertIn('anything_requests_total{type="agent"} 2', text)

    def test_metrics_prometheus_format_well_formed(self):
        """简单检查输出能被 prometheus 文本格式 parser 接受"""
        self.client.post("/invoke", json={"type": "rag", "query": "x"})
        text = self.client.get("/metrics").text
        # 每一组 metric 应该有 # HELP 和 # TYPE 行
        lines = text.split("\n")
        help_count = sum(1 for l in lines if l.startswith("# HELP"))
        type_count = sum(1 for l in lines if l.startswith("# TYPE"))
        self.assertGreaterEqual(help_count, 4)
        self.assertGreaterEqual(type_count, 4)


if __name__ == "__main__":
    unittest.main()
