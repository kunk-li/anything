# -*- coding: utf-8 -*-
"""外部工具连接 RFC · 增量: OpenAPI → HTTP 工具自动生成 (复用 Stage1 HttpToolProvider)。"""
import json
import unittest
from unittest import mock

from agent_module.tools.external import (
    openapi_to_specs, build_providers_from_config, HttpToolProvider, fetch_openapi_spec,
)
from agent_module.tools.external.http_provider import make_http_tool

_SAMPLE = {
    "servers": [{"url": "https://api.example.com/v1"}],
    "paths": {
        "/users/{id}": {
            "get": {"operationId": "getUser", "summary": "get a user",
                    "parameters": [{"name": "id", "in": "path"}, {"name": "fields", "in": "query"}]},
        },
        "/users": {
            "post": {"operationId": "createUser",
                     "requestBody": {"content": {"application/json": {
                         "schema": {"properties": {"name": {}, "email": {}}}}}}},
        },
        "/ping": {"get": {}},                       # 无 operationId → 派生
    },
}


class TestOpenapiToSpecs(unittest.TestCase):
    def _by_name(self, **kw):
        return {s.name: s for s in openapi_to_specs(_SAMPLE, **kw)}

    def test_get_path_and_query(self):
        g = self._by_name()["getUser"]
        self.assertEqual(g.method, "GET")
        self.assertEqual(g.url, "https://api.example.com/v1/users/{id}")   # path 占位保留
        self.assertEqual(g.query, ["fields"])
        self.assertTrue(g.requires_approval)

    def test_post_body(self):
        p = self._by_name()["createUser"]
        self.assertEqual(p.method, "POST")
        self.assertEqual(p.url, "https://api.example.com/v1/users")
        self.assertEqual(sorted(p.body), ["email", "name"])

    def test_derive_op_id(self):
        self.assertIn("get_ping", self._by_name())              # 无 operationId → 派生

    def test_base_url_override_and_prefix(self):
        urls = {s.name: s.url for s in openapi_to_specs(_SAMPLE, base_url="https://proxy.local", name_prefix="ext_")}
        self.assertEqual(urls["ext_getUser"], "https://proxy.local/users/{id}")

    def test_empty_or_invalid(self):
        self.assertEqual(openapi_to_specs({}), [])
        self.assertEqual(openapi_to_specs({"paths": "nope"}), [])
        self.assertEqual(openapi_to_specs("not a dict"), [])

    def test_generated_tool_callable(self):
        # 生成 spec → make_http_tool → 注入 fake fetch (复用 Stage1); 公网 IP 避 DNS
        g = openapi_to_specs(_SAMPLE, base_url="https://1.1.1.1")[0]   # getUser (paths 顺序首个)
        calls = []
        def fake(method, url, headers, body, timeout, max_bytes):
            calls.append(url)
            return 200, '{"ok": 1}'
        r = make_http_tool(g, fetch=fake)({"id": "42", "fields": "name"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertIn("/users/42", calls[0])
        self.assertIn("fields=name", calls[0])


class TestBuildOpenapiFromConfig(unittest.TestCase):
    def test_openapi_tools_config_builds_provider(self):
        class _Cfg:
            def get_config(self, k, default=None):
                if k == "agent.openapi_tools":
                    return [{"spec": _SAMPLE, "base_url": "https://1.1.1.1", "name_prefix": "api_"}]
                return default
        providers = build_providers_from_config(_Cfg())
        self.assertEqual(len(providers), 1)
        self.assertIsInstance(providers[0], HttpToolProvider)
        names = [d.name for d in providers[0].discover()]
        self.assertIn("api_getUser", names)
        self.assertIn("api_createUser", names)


class TestFetchOpenapiSpec(unittest.TestCase):
    """spec_url 远程拉取: SSRF 安全 + JSON/YAML 解析 (fetch 注入, 不发真网络)。"""

    def test_fetch_json_spec(self):
        def fake(method, url, headers, body, timeout, max_bytes):
            return 200, json.dumps(_SAMPLE)
        spec = fetch_openapi_spec("https://1.1.1.1/openapi.json", fetch=fake)
        self.assertIn("paths", spec)

    def test_non_2xx_raises(self):
        def fake(*a):
            return 404, "not found"
        with self.assertRaises(RuntimeError):
            fetch_openapi_spec("https://1.1.1.1/x", fetch=fake)

    def test_ssrf_private_ip_rejected_before_fetch(self):
        calls = []
        def fake(*a):
            calls.append(1)
            return 200, "{}"
        with self.assertRaises(ValueError):
            fetch_openapi_spec("http://127.0.0.1/openapi.json", fetch=fake)
        self.assertEqual(calls, [])          # SSRF 校验在 fetch 前, 私网根本不发请求

    def test_non_dict_top_level_raises(self):
        def fake(*a):
            return 200, "[1, 2, 3]"
        with self.assertRaises(ValueError):
            fetch_openapi_spec("https://1.1.1.1/x", fetch=fake)

    def test_yaml_fallback(self):
        try:
            import yaml  # noqa: F401
        except Exception:
            self.skipTest("PyYAML 不可用, 跳过 YAML 退化")
        def fake(*a):
            return 200, "openapi: 3.0.0\npaths: {}\n"
        spec = fetch_openapi_spec("https://1.1.1.1/x", fetch=fake)
        self.assertEqual(spec["openapi"], "3.0.0")


class TestOpenapiSpecUrlFromConfig(unittest.TestCase):
    """build_providers_from_config 走 spec_url 分支 (mock 掉远程拉取)。"""

    class _Cfg:
        def __init__(self, entry):
            self._entry = entry
        def get_config(self, k, default=None):
            return [self._entry] if k == "agent.openapi_tools" else default

    def test_spec_url_fetched_and_built(self):
        cfg = self._Cfg({"spec_url": "https://1.1.1.1/openapi.json",
                         "base_url": "https://1.1.1.1", "name_prefix": "rem_"})
        with mock.patch("agent_module.tools.external.fetch_openapi_spec",
                        return_value=_SAMPLE) as m:
            providers = build_providers_from_config(cfg)
        m.assert_called_once()
        self.assertEqual(len(providers), 1)
        names = [d.name for d in providers[0].discover()]
        self.assertIn("rem_getUser", names)

    def test_spec_url_fetch_failure_skipped(self):
        # 拉取/解析失败 → 该项 fail-safe 跳过 → 无 provider (不阻断启动)
        cfg = self._Cfg({"spec_url": "https://1.1.1.1/bad"})
        with mock.patch("agent_module.tools.external.fetch_openapi_spec",
                        side_effect=RuntimeError("boom")):
            providers = build_providers_from_config(cfg)
        self.assertEqual(providers, [])

    def test_spec_url_auth_passed_to_fetch(self):
        # entry 的 auth_* 应作为拉取 spec 的请求头传给 fetch
        cfg = self._Cfg({"spec_url": "https://1.1.1.1/openapi.json",
                         "auth_header": "Authorization", "auth_value": "Bearer T"})
        with mock.patch("agent_module.tools.external.fetch_openapi_spec",
                        return_value=_SAMPLE) as m:
            build_providers_from_config(cfg)
        _, kwargs = m.call_args
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer T"})


if __name__ == "__main__":
    unittest.main()
