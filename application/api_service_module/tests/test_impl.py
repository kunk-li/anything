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
