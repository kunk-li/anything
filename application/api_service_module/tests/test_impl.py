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
        # PR4a 后输出多标签 type + tenant; 未传 tenant_id 走 default
        self.assertIn('anything_requests_total{type="rag",tenant="default"} 1', text)
        self.assertIn('anything_request_duration_seconds_count{type="rag",tenant="default"} 1', text)
        # SUCCESS 不应进 errors_total
        self.assertNotIn('anything_errors_total{code=', text)

    def test_metrics_after_error_invoke(self):
        # 触发一次 PARAM_MISSING 请求
        self.client.post("/invoke", json={"type": "rag"})
        response = self.client.get("/metrics")
        text = response.text
        self.assertIn('anything_requests_total{type="rag",tenant="default"} 1', text)
        self.assertIn('anything_errors_total{code="PARAM_MISSING",tenant="default"} 1', text)

    def test_metrics_accumulates_across_requests(self):
        for _ in range(3):
            self.client.post("/invoke", json={"type": "rag", "query": "x"})
        for _ in range(2):
            self.client.post("/invoke", json={"type": "agent", "task": "y"})
        text = self.client.get("/metrics").text
        self.assertIn('anything_requests_total{type="rag",tenant="default"} 3', text)
        self.assertIn('anything_requests_total{type="agent",tenant="default"} 2', text)

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


class TestMetricsTenantLabel(unittest.TestCase):
    """Task #33 PR4a: metrics 增加 tenant 标签 + cardinality top_n 守护"""

    def setUp(self):
        self.handler = MockHandler()
        self.service = ApiService(handler=self.handler)
        self.service.auth_enabled = False
        # PR4b: 把所有测试用 tenant 加入 known_tenants 集合, 否则会被 404 拦截
        self.service._known_tenants.update({
            "tenant-a", "tenant-b", "tenant-zz", "tenant-zy",
        })
        self.client = TestClient(self.service.app)

    def test_metrics_tenant_label_in_output(self):
        """body 显式带 tenant_id 时, metrics 应该用该 tenant 标签"""
        # 注意 PR2 已经把 auth 关掉, 这里走 external IP path:
        # body tenant_id 会被 _reconcile 剥掉 (非 internal IP) -> 走 default.
        # 为了测 tenant 标签真正能写到 metrics, 把 IP 加 internal whitelist;
        # 同时把 tenant-a 加入 metrics allowlist (否则会被聚合成 "other")。
        self.service.internal_whitelist = ["testclient"]
        self.service._tenant_label_allowlist = {"default", "tenant-a"}
        # 1 个 tenant-a 请求
        self.client.post(
            "/invoke",
            json={"type": "rag", "query": "x", "tenant_id": "tenant-a"},
        )
        text = self.client.get("/metrics").text
        self.assertIn('anything_requests_total{type="rag",tenant="tenant-a"} 1', text)

    def test_metrics_tenant_separation(self):
        """两个不同 tenant 的请求应该分别累计"""
        self.service.internal_whitelist = ["testclient"]
        self.service._tenant_label_allowlist = {"default", "tenant-a", "tenant-b"}
        for _ in range(2):
            self.client.post(
                "/invoke",
                json={"type": "rag", "query": "x", "tenant_id": "tenant-a"},
            )
        for _ in range(3):
            self.client.post(
                "/invoke",
                json={"type": "rag", "query": "y", "tenant_id": "tenant-b"},
            )
        text = self.client.get("/metrics").text
        self.assertIn('anything_requests_total{type="rag",tenant="tenant-a"} 2', text)
        self.assertIn('anything_requests_total{type="rag",tenant="tenant-b"} 3', text)

    def test_metrics_cardinality_top_n_aggregation(self):
        """超出 top_n 的 tenant 应被聚合为 tenant=other"""
        # 手动收紧 allowlist 模拟 top_n=2
        self.service._tenant_label_allowlist = {"default", "tenant-a"}
        self.service.internal_whitelist = ["testclient"]
        # tenant-a 在 allowlist 内
        self.client.post(
            "/invoke", json={"type": "rag", "query": "x", "tenant_id": "tenant-a"},
        )
        # tenant-zz 不在 allowlist -> 应聚合为 other
        self.client.post(
            "/invoke", json={"type": "rag", "query": "x", "tenant_id": "tenant-zz"},
        )
        self.client.post(
            "/invoke", json={"type": "rag", "query": "x", "tenant_id": "tenant-zy"},
        )
        text = self.client.get("/metrics").text
        self.assertIn('anything_requests_total{type="rag",tenant="tenant-a"} 1', text)
        self.assertIn('anything_requests_total{type="rag",tenant="other"} 2', text)
        # 严格不应该有 tenant-zz / tenant-zy 单独标签
        self.assertNotIn('tenant="tenant-zz"', text)
        self.assertNotIn('tenant="tenant-zy"', text)

    def test_bucket_tenant_helper(self):
        """_bucket_tenant 单元测试: allowlist 内保留, 外聚合 other"""
        self.service._tenant_label_allowlist = {"default", "tenant-a"}
        self.assertEqual(self.service._bucket_tenant("tenant-a"), "tenant-a")
        self.assertEqual(self.service._bucket_tenant("default"), "default")
        self.assertEqual(self.service._bucket_tenant("tenant-zz"), "other")
        # None / 空字符串 -> default
        self.assertEqual(self.service._bucket_tenant(None), "default")
        self.assertEqual(self.service._bucket_tenant(""), "default")


class TestContextVarIsolation(unittest.TestCase):
    """Task #33 PR4a §7.3.1: tenant ContextVar 必须请求间隔离"""

    def setUp(self):
        self.handler = MockHandler()
        self.service = ApiService(handler=self.handler)
        self.service.auth_enabled = False
        self.service.internal_whitelist = ["testclient"]
        # PR4b: 把测试用 tenant 加入 known_tenants, 否则会被 404 拦截
        self.service._known_tenants.update({"tenant-a", "tenant-b"})
        self.client = TestClient(self.service.app)

    def test_contextvar_does_not_leak_across_requests(self):
        """请求 A 设的 tenant_id 不应被请求 B 看到"""
        from observability_module import get_current_tenant

        # 在外层进程上下文里 (没有请求时) tenant 应是 None
        self.assertIsNone(get_current_tenant())

        # 用 handler 当 hook: handle 时记录当时的 ContextVar 值
        seen_tids = []

        class _HookHandler(MockHandler):
            def handle(self, request, trace_id=None):
                seen_tids.append(get_current_tenant())
                return super().handle(request, trace_id=trace_id)

        self.service.handler = _HookHandler()

        # 发请求 A (tenant-a)
        self.client.post(
            "/invoke", json={"type": "rag", "query": "a", "tenant_id": "tenant-a"},
        )
        # 发请求 B (tenant-b)
        self.client.post(
            "/invoke", json={"type": "rag", "query": "b", "tenant_id": "tenant-b"},
        )
        # 不带 tenant_id 的请求 (走 default)
        self.client.post("/invoke", json={"type": "rag", "query": "c"})

        self.assertEqual(seen_tids, ["tenant-a", "tenant-b", "default"])

        # 所有请求处理完后 ContextVar 应该被 reset 回 None
        self.assertIsNone(get_current_tenant())

    def test_contextvar_reset_on_handler_exception(self):
        """handler 抛异常时 ContextVar 也必须被 reset"""
        from observability_module import get_current_tenant

        class _BoomHandler(MockHandler):
            def handle(self, request, trace_id=None):
                raise RuntimeError("boom")

        self.service.handler = _BoomHandler()

        try:
            self.client.post(
                "/invoke",
                json={"type": "rag", "query": "x", "tenant_id": "tenant-a"},
            )
        except Exception:
            pass

        # 即使 handler 炸了, ContextVar 必须回到 None
        self.assertIsNone(get_current_tenant())


class TestQuotaAndTenantNotFound(unittest.TestCase):
    """Task #33 PR4b: TENANT_NOT_FOUND + QPS 滑窗限流"""

    def setUp(self):
        self.handler = MockHandler()
        self.service = ApiService(handler=self.handler)
        self.service.auth_enabled = False
        self.service.internal_whitelist = ["testclient"]
        self.client = TestClient(self.service.app)

    def test_known_tenant_passes(self):
        """default 在 _known_tenants 中, 请求应该正常通过"""
        # 默认无 quota config 时 _known_tenants = {'default'}
        self.assertIn("default", self.service._known_tenants)
        response = self.client.post("/invoke", json={"type": "rag", "query": "x"})
        self.assertEqual(response.status_code, 200)

    def test_unknown_tenant_returns_tenant_not_found_404(self):
        """body 显式声明的未知 tenant -> 404 TENANT_NOT_FOUND"""
        # 此时 _known_tenants 仅 {'default'}, body 声明 tenant-xyz 不在
        response = self.client.post(
            "/invoke",
            json={"type": "rag", "query": "x", "tenant_id": "tenant-xyz"},
        )
        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertEqual(payload["code"], "TENANT_NOT_FOUND")
        # §9.3: details 不暴露存在性
        self.assertIsNone(payload["details"])

    def test_known_tenant_after_quota_config(self):
        """quotas 配置里出现的 tenant 自动加入 _known_tenants"""
        # 模拟 reload 后, 把 tenant-a 加入 known
        self.service._known_tenants.add("tenant-a")
        response = self.client.post(
            "/invoke",
            json={"type": "rag", "query": "x", "tenant_id": "tenant-a"},
        )
        self.assertEqual(response.status_code, 200)

    def test_qps_quota_no_config_passes(self):
        """没配 max_qps -> 不限流"""
        # 默认配置无 quotas.default.max_qps
        for _ in range(10):
            response = self.client.post("/invoke", json={"type": "rag", "query": "x"})
            self.assertEqual(response.status_code, 200)

    def test_qps_quota_under_limit_passes(self):
        """配 max_qps=10 跑 5 次 -> 全过"""
        original_get = self.service.config.get_config

        def patched(key, default=None):
            if key == "quotas.default.max_qps":
                return 10
            return original_get(key, default)

        self.service.config.get_config = patched
        for _ in range(5):
            r = self.client.post("/invoke", json={"type": "rag", "query": "x"})
            self.assertEqual(r.status_code, 200)

    def test_qps_quota_over_limit_429(self):
        """配 max_qps=2 跑 5 次 -> 后 3 次应该 429 API_RATE_LIMITED"""
        original_get = self.service.config.get_config

        def patched(key, default=None):
            if key == "quotas.default.max_qps":
                return 2
            return original_get(key, default)

        self.service.config.get_config = patched

        statuses = []
        for _ in range(5):
            r = self.client.post("/invoke", json={"type": "rag", "query": "x"})
            statuses.append(r.status_code)
        # 前 2 个应 200, 后 3 个 429
        self.assertEqual(statuses.count(200), 2)
        self.assertEqual(statuses.count(429), 3)

        # 检查 429 响应体格式
        r_last = self.client.post("/invoke", json={"type": "rag", "query": "x"})
        self.assertEqual(r_last.status_code, 429)
        payload = r_last.json()
        self.assertEqual(payload["code"], "API_RATE_LIMITED")
        self.assertTrue(payload["retryable"])
        self.assertIn("Retry-After", r_last.headers)

    def test_qps_window_slides_after_1s(self):
        """1 秒窗口: 等 1.1s 后再发, 旧 timestamp 被弹出, 应能继续"""
        import time as _t
        original_get = self.service.config.get_config

        def patched(key, default=None):
            if key == "quotas.default.max_qps":
                return 1
            return original_get(key, default)

        self.service.config.get_config = patched

        r1 = self.client.post("/invoke", json={"type": "rag", "query": "x"})
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post("/invoke", json={"type": "rag", "query": "x"})
        self.assertEqual(r2.status_code, 429)
        # 等窗口滑过
        _t.sleep(1.1)
        r3 = self.client.post("/invoke", json={"type": "rag", "query": "x"})
        self.assertEqual(r3.status_code, 200)

    def test_qps_per_tenant_isolated(self):
        """tenant-a 限 1 QPS 不应影响 tenant-b"""
        self.service._known_tenants.update({"tenant-a", "tenant-b"})
        original_get = self.service.config.get_config

        def patched(key, default=None):
            if key in ("quotas.tenant-a.max_qps", "quotas.tenant-b.max_qps"):
                return 1
            return original_get(key, default)

        self.service.config.get_config = patched

        # tenant-a 第二次应被限
        ra1 = self.client.post(
            "/invoke", json={"type": "rag", "query": "x", "tenant_id": "tenant-a"},
        )
        ra2 = self.client.post(
            "/invoke", json={"type": "rag", "query": "x", "tenant_id": "tenant-a"},
        )
        self.assertEqual(ra1.status_code, 200)
        self.assertEqual(ra2.status_code, 429)
        # tenant-b 仍允许第一次
        rb1 = self.client.post(
            "/invoke", json={"type": "rag", "query": "x", "tenant_id": "tenant-b"},
        )
        self.assertEqual(rb1.status_code, 200)


class TestWebSocketStream(unittest.TestCase):
    """WS /invoke/stream — 简化版流式 (跑完 sync handler 后切片发)"""

    def setUp(self):
        self.handler = MockHandler()

    def test_stream_success_flow(self):
        """成功流: start -> chunk * N -> metadata -> done"""
        class _H:
            def handle(self, req, trace_id=None):
                return {
                    "code": "SUCCESS",
                    "message": "ok",
                    "data": {
                        "answer": "Hello WebSocket streaming world. " * 3,
                        "citations": [{"chunk_id": "c1", "doc_id": "d1"}],
                        "retrieved_chunks": [{"chunk_id": "c1", "doc_id": "d1", "score": 0.9}],
                        "steps": [],
                    },
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                }

        svc = ApiService(handler=_H())
        svc.auth_enabled = False
        client = TestClient(svc.app)

        with client.websocket_connect("/invoke/stream") as ws:
            ws.send_json({"type": "rag", "query": "hi"})

            msgs = []
            while True:
                try:
                    m = ws.receive_json()
                except Exception:
                    break
                msgs.append(m)
                if m.get("type") in ("done", "error"):
                    break

        types = [m["type"] for m in msgs]
        self.assertEqual(types[0], "start")
        self.assertGreaterEqual(types.count("chunk"), 1)
        self.assertIn("metadata", types)
        self.assertEqual(types[-1], "done")
        # 拼接 chunks 应跟原 answer 相等
        joined = "".join(m["text"] for m in msgs if m["type"] == "chunk")
        self.assertEqual(joined, "Hello WebSocket streaming world. " * 3)
        # metadata 段带 citations
        meta = next(m for m in msgs if m["type"] == "metadata")
        self.assertEqual(len(meta["citations"]), 1)

    def test_stream_error_propagates(self):
        """handler 返回非 SUCCESS code -> WS 发 error 消息"""
        class _H:
            def handle(self, req, trace_id=None):
                return {
                    "code": "PARAM_MISSING",
                    "message": "缺 query",
                    "data": None,
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": {"field": "query"},
                }

        svc = ApiService(handler=_H())
        svc.auth_enabled = False
        client = TestClient(svc.app)
        with client.websocket_connect("/invoke/stream") as ws:
            ws.send_json({"type": "rag"})
            m_start = ws.receive_json()
            m_err = ws.receive_json()
            self.assertEqual(m_start["type"], "start")
            self.assertEqual(m_err["type"], "error")
            self.assertEqual(m_err["code"], "PARAM_MISSING")

    def test_stream_unknown_tenant_rejected(self):
        """未知 tenant 立即 error TENANT_NOT_FOUND (不进 handler)"""
        svc = ApiService(handler=self.handler)
        svc.auth_enabled = False
        svc.internal_whitelist = ["testclient"]
        client = TestClient(svc.app)
        with client.websocket_connect("/invoke/stream") as ws:
            ws.send_json({"type": "rag", "query": "x", "tenant_id": "tenant-ghost"})
            # 应直接收到 start? 不, 我们在 reconcile 后立即拦截.
            # 看实现: start 之前先做 tenant_not_found 检查
            m = ws.receive_json()
            self.assertEqual(m["type"], "error")
            self.assertEqual(m["code"], "TENANT_NOT_FOUND")

    def test_stream_auth_required_for_apikey(self):
        """auth_enabled=apikey 但 querystring 无 api_key -> 4401 close"""
        svc = ApiService(handler=self.handler)
        svc.auth_enabled = True
        svc.auth_type = "apikey"
        svc._key_to_tenant = {"key_a1": "default"}
        client = TestClient(svc.app)
        # connect without api_key -> close 4401
        # TestClient 的 ws 在 close 时直接抛 WebSocketDisconnect
        with self.assertRaises(Exception):
            with client.websocket_connect("/invoke/stream") as ws:
                ws.receive_json()  # 不会发到这一步

    def test_stream_auth_apikey_in_querystring(self):
        """正确 api_key querystring -> 接受 + 走完流程"""
        class _H:
            def handle(self, req, trace_id=None):
                return {"code": "SUCCESS", "message": "ok",
                        "data": {"answer": "ok"},
                        "trace_id": trace_id, "retryable": False, "details": None}

        svc = ApiService(handler=_H())
        svc.auth_enabled = True
        svc.auth_type = "apikey"
        svc._key_to_tenant = {"key_a1": "default"}
        client = TestClient(svc.app)
        with client.websocket_connect("/invoke/stream?api_key=key_a1") as ws:
            ws.send_json({"type": "rag", "query": "x"})
            msgs = []
            while True:
                try:
                    msgs.append(ws.receive_json())
                except Exception:
                    break
                if msgs[-1].get("type") in ("done", "error"):
                    break
        types = [m["type"] for m in msgs]
        self.assertEqual(types[0], "start")
        self.assertEqual(types[-1], "done")


class TestDevModeAuthAutoDisable(unittest.TestCase):
    """DEV_MODE 启动 + yaml api_keys 全是未解析 ${ENV} 占位符 -> 自动关 auth"""

    def test_dev_mode_with_placeholder_keys_disables_auth(self):
        """DEV_MODE=1 + key 列表全是 ${X} 字面量 -> auth_enabled 应被自动关掉"""
        # 通过 patching ApiService 内部读到的 config 来模拟 yaml 状态
        from unittest.mock import patch
        # 默认 ApiService 走 build_basic_deps + config_module, 其 security.api_keys 是
        # ["${API_KEY_1}"] (因为环境变量没设, 占位符未被替换)。DEV_MODE 是 setUp 已设。
        # 直接构造看 auth_enabled 是否变 False:
        svc = ApiService(handler=MockHandler())
        # 老 yaml 默认 auth_enabled=true + auth_type=apikey + 1 个占位符 key
        # -> 启动期自动关 auth
        self.assertFalse(svc.auth_enabled, "DEV_MODE + 占位符 key 时应自动关 auth")
        # _key_to_tenant 反向索引仍构建好了 (字面量 ${API_KEY_1} -> default)
        self.assertIn("${API_KEY_1}", svc._key_to_tenant)

    def test_dev_mode_with_real_keys_keeps_auth_on(self):
        """DEV_MODE=1 但 yaml 里全是真实 key 时, auth 不自动关 (用户显式想用 auth)"""
        # patch _build_key_to_tenant_index 让它返回非占位符的真实 key
        from unittest.mock import patch
        with patch.object(
            ApiService, "_build_key_to_tenant_index",
            return_value={"real-key-123": "default"},
        ):
            svc = ApiService(handler=MockHandler())
        # auth_enabled 应保留 yaml 配置 (true)
        self.assertTrue(svc.auth_enabled, "有真实 key 时应保持 auth_enabled=true")


class TestModelConfigEndpoints(unittest.TestCase):
    """运行期 LLM 模型注册表管理: GET/POST/DELETE /config/models + set-default"""

    def setUp(self):
        self.handler = MockHandler()

    def test_endpoints_unavailable_without_llm_service(self):
        """没注入 llm_service 时所有 /config/models* 端点返回 501"""
        svc = ApiService(handler=self.handler)
        svc.auth_enabled = False
        client = TestClient(svc.app)
        for path, method in [
            ("/config/models", "GET"),
            ("/config/models", "POST"),
            ("/config/models/whatever", "DELETE"),
            ("/config/models/whatever/set-default", "POST"),
        ]:
            r = client.request(method, path, json={})
            self.assertEqual(r.status_code, 501, path)
            self.assertEqual(r.json()["code"], "SERVICE_UNAVAILABLE")

    def test_list_models(self):
        """GET /config/models 返回脱敏 key 的列表"""
        class _LLM:
            def list_models(self, *, mask_keys=True):
                assert mask_keys is True
                return [
                    {
                        "name": "qwen-turbo",
                        "request_type": "CHAT",
                        "adapter_class": "OpenAIChatAdapter",
                        "api_base": "https://x",
                        "api_key": "sk-****c1a",
                        "configured": True,
                        "is_default": True,
                    }
                ]

        svc = ApiService(handler=self.handler, llm_service=_LLM())
        svc.auth_enabled = False
        client = TestClient(svc.app)
        r = client.get("/config/models")
        self.assertEqual(r.status_code, 200)
        d = r.json()["data"]
        self.assertEqual(len(d["models"]), 1)
        self.assertEqual(d["models"][0]["name"], "qwen-turbo")
        # api_key 被脱敏
        self.assertIn("****", d["models"][0]["api_key"])

    def test_register_model(self):
        """POST /config/models 注册新模型"""
        captured = {}

        class _LLM:
            def list_models(self, *, mask_keys=True):
                return []

            def register_or_update_model(self, name, request_type, adapter_class,
                                          api_key, api_base, *, extra=None, set_as_default=False):
                captured.update({
                    "name": name, "request_type": request_type,
                    "adapter_class": adapter_class, "api_key": api_key,
                    "api_base": api_base, "set_as_default": set_as_default,
                })
                return {
                    "name": name, "request_type": request_type,
                    "adapter_class": adapter_class,
                    "api_base": api_base, "api_key": "sk-****",
                    "configured": True, "is_default": set_as_default,
                }

        svc = ApiService(handler=self.handler, llm_service=_LLM())
        svc.auth_enabled = False
        client = TestClient(svc.app)
        r = client.post("/config/models", json={
            "name": "qwen-plus",
            "request_type": "chat",
            "adapter_class": "OpenAIChatAdapter",
            "api_key": "sk-real-secret",
            "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "set_as_default": True,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["code"], "SUCCESS")
        # impl 收到的 request_type 被 upper 后变 CHAT
        self.assertEqual(captured["request_type"], "CHAT")
        self.assertEqual(captured["set_as_default"], True)

    def test_register_model_missing_required(self):
        """POST /config/models 缺必填字段 -> 400 PARAM_MISSING"""
        class _LLM:
            def list_models(self, *, mask_keys=True): return []
            def register_or_update_model(self, **kw): raise ValueError("不应到这里")

        svc = ApiService(handler=self.handler, llm_service=_LLM())
        svc.auth_enabled = False
        client = TestClient(svc.app)
        r = client.post("/config/models", json={"name": "only-name"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "PARAM_MISSING")

    def test_register_model_invalid_param(self):
        """impl raise ValueError -> 400 PARAM_INVALID"""
        class _LLM:
            def list_models(self, *, mask_keys=True): return []
            def register_or_update_model(self, **kw):
                raise ValueError("adapter_class 未知")

        svc = ApiService(handler=self.handler, llm_service=_LLM())
        svc.auth_enabled = False
        client = TestClient(svc.app)
        r = client.post("/config/models", json={
            "name": "x", "request_type": "CHAT", "adapter_class": "UnknownAdapter",
        })
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "PARAM_INVALID")
        self.assertIn("adapter_class 未知", r.json()["message"])

    def test_delete_model(self):
        class _LLM:
            def list_models(self, *, mask_keys=True): return []
            def unregister_model(self, name): return name == "exists"

        svc = ApiService(handler=self.handler, llm_service=_LLM())
        svc.auth_enabled = False
        client = TestClient(svc.app)
        r1 = client.delete("/config/models/exists")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()["code"], "SUCCESS")
        r2 = client.delete("/config/models/missing")
        self.assertEqual(r2.status_code, 404)
        self.assertEqual(r2.json()["code"], "MODEL_NOT_FOUND")

    def test_set_default(self):
        class _LLM:
            def list_models(self, *, mask_keys=True): return []
            def set_default_model(self, name, request_type=None):
                if name == "ghost":
                    raise ValueError("不存在")
                return {"request_type": "CHAT", "default_model": name}

        svc = ApiService(handler=self.handler, llm_service=_LLM())
        svc.auth_enabled = False
        client = TestClient(svc.app)
        r = client.post("/config/models/qwen-turbo/set-default", json={"request_type": "CHAT"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["default_model"], "qwen-turbo")

        r2 = client.post("/config/models/ghost/set-default", json={})
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(r2.json()["code"], "PARAM_INVALID")


class TestDocumentPreview(unittest.TestCase):
    """GET /documents/{doc_id}/preview — 跟 chunk 跳转回原文功能配套"""

    def setUp(self):
        self.handler = MockHandler()

    def test_preview_not_supported_when_factory_missing(self):
        """没注入 document_store_factory 时返回 501 PREVIEW_NOT_SUPPORTED"""
        svc = ApiService(handler=self.handler)
        svc.auth_enabled = False
        client = TestClient(svc.app)
        r = client.get("/documents/00000000-0000-4000-8000-000000000000/preview")
        self.assertEqual(r.status_code, 501)
        self.assertEqual(r.json()["code"], "PREVIEW_NOT_SUPPORTED")

    def test_preview_returns_snippet_with_highlight(self):
        """注入 factory 后, 正常 doc 应返回 snippet + highlight 偏移"""
        class _FakeStore:
            def get_document(self, doc_id):
                if doc_id == "missing":
                    return None
                # 模拟 1000 char 长的文档内容
                return {
                    "doc_id": doc_id,
                    "file_name": "demo.md",
                    "file_type": "md",
                    "content": "X" * 1000,
                }

        svc = ApiService(handler=self.handler, document_store_factory=lambda tid: _FakeStore())
        svc.auth_enabled = False
        # tenant 校验放过
        svc._known_tenants.add("default")
        client = TestClient(svc.app)

        r = client.get(
            "/documents/00000000-0000-4000-8000-000000000000/preview"
            "?start_char=500&end_char=520&context=100"
        )
        self.assertEqual(r.status_code, 200)
        payload = r.json()
        self.assertEqual(payload["code"], "SUCCESS")
        d = payload["data"]
        self.assertEqual(d["file_name"], "demo.md")
        self.assertEqual(d["total_chars"], 1000)
        # snippet_start = 500 - 100 = 400, snippet_end = 520 + 100 = 620
        self.assertEqual(d["snippet_start"], 400)
        self.assertEqual(d["snippet_end"], 620)
        # 偏移 (相对 snippet)
        self.assertEqual(d["highlight_start"], 100)
        self.assertEqual(d["highlight_end"], 120)
        self.assertEqual(len(d["snippet"]), 220)

    def test_preview_404_when_doc_missing(self):
        """doc 不存在 -> 404 DOCUMENT_NOT_FOUND (§9.3 防枚举, 不区分跨租户/不存在)"""
        class _Empty:
            def get_document(self, doc_id):
                return None

        svc = ApiService(handler=self.handler, document_store_factory=lambda tid: _Empty())
        svc.auth_enabled = False
        svc._known_tenants.add("default")
        client = TestClient(svc.app)
        r = client.get("/documents/00000000-0000-4000-8000-000000000000/preview")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "DOCUMENT_NOT_FOUND")

    def test_preview_invalid_doc_id(self):
        """非法 doc_id (raise ValueError) -> 400 PARAM_INVALID"""
        class _Raises:
            def get_document(self, doc_id):
                raise ValueError("doc_id格式非法,需为UUID4")

        svc = ApiService(handler=self.handler, document_store_factory=lambda tid: _Raises())
        svc.auth_enabled = False
        svc._known_tenants.add("default")
        client = TestClient(svc.app)
        r = client.get("/documents/not-a-uuid/preview")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "PARAM_INVALID")

    def test_preview_unknown_tenant_404(self):
        """未知 tenant -> 404 TENANT_NOT_FOUND (§9.3 同桶防枚举)"""
        class _Store:
            def get_document(self, doc_id):
                return {"content": "x", "file_name": "a.md"}

        svc = ApiService(handler=self.handler, document_store_factory=lambda tid: _Store())
        svc.auth_enabled = False
        svc.internal_whitelist = ["testclient"]  # 允许 query tenant
        # _known_tenants 默认仅 {default}
        client = TestClient(svc.app)
        r = client.get(
            "/documents/00000000-0000-4000-8000-000000000000/preview?tenant_id=tenant-ghost"
        )
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "TENANT_NOT_FOUND")


class TestFrontendMount(unittest.TestCase):
    """Web UI 挂载: GET / 返回 index.html, /static/* 提供静态资源"""

    def setUp(self):
        self.handler = MockHandler()
        self.service = ApiService(handler=self.handler)
        self.service.auth_enabled = False
        self.client = TestClient(self.service.app)

    def test_root_returns_html_when_frontend_exists(self):
        """frontend/index.html 存在时 GET / 应返回 HTML"""
        import os
        from pathlib import Path
        # 找 frontend dir, 跟 _mount_frontend 用同样的 fallback
        candidates = [
            Path(__file__).resolve().parents[3] / "frontend",
            Path.cwd() / "frontend",
            Path.cwd().parent / "frontend",
        ]
        frontend_exists = any((p / "index.html").exists() for p in candidates)
        if not frontend_exists:
            self.skipTest("frontend/index.html 不存在, 跳过 (纯 API 部署场景)")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        self.assertIn("Anything", resp.text)

    def test_static_assets_served(self):
        """静态资源应可访问"""
        from pathlib import Path
        candidates = [
            Path(__file__).resolve().parents[3] / "frontend" / "static",
            Path.cwd() / "frontend" / "static",
            Path.cwd().parent / "frontend" / "static",
        ]
        static_exists = any((p / "app.js").exists() for p in candidates)
        if not static_exists:
            self.skipTest("frontend/static/app.js 不存在")
        resp = self.client.get("/static/app.js")
        self.assertEqual(resp.status_code, 200)
        # JS 文件 content-type
        self.assertTrue(
            "javascript" in resp.headers.get("content-type", "")
            or "text" in resp.headers.get("content-type", "")
        )

    def test_ui_alias_works(self):
        """GET /ui 应跟 / 等价"""
        from pathlib import Path
        candidates = [
            Path(__file__).resolve().parents[3] / "frontend",
            Path.cwd() / "frontend",
            Path.cwd().parent / "frontend",
        ]
        if not any((p / "index.html").exists() for p in candidates):
            self.skipTest("frontend/index.html 不存在")
        resp = self.client.get("/ui")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))


class TestHTTPStatusMapping(unittest.TestCase):
    """PR4b: 新错误码 -> HTTP 状态码映射"""

    def setUp(self):
        self.handler = MockHandler()
        self.service = ApiService(handler=self.handler)

    def test_tenant_required_401(self):
        self.assertEqual(self.service._map_code_to_http_status("TENANT_REQUIRED"), 401)

    def test_tenant_not_found_404(self):
        self.assertEqual(self.service._map_code_to_http_status("TENANT_NOT_FOUND"), 404)

    def test_quota_doc_exceeded_429(self):
        self.assertEqual(self.service._map_code_to_http_status("QUOTA_DOC_EXCEEDED"), 429)

    def test_quota_storage_exceeded_429(self):
        self.assertEqual(self.service._map_code_to_http_status("QUOTA_STORAGE_EXCEEDED"), 429)

    def test_legacy_codes_unchanged(self):
        """原有码不应受影响"""
        self.assertEqual(self.service._map_code_to_http_status("SUCCESS"), 200)
        self.assertEqual(self.service._map_code_to_http_status("PARAM_MISSING"), 400)
        self.assertEqual(self.service._map_code_to_http_status("AUTH_REQUIRED"), 401)
        self.assertEqual(self.service._map_code_to_http_status("DOCUMENT_NOT_FOUND"), 404)
        self.assertEqual(self.service._map_code_to_http_status("API_RATE_LIMITED"), 429)


if __name__ == "__main__":
    unittest.main()
