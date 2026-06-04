# -*- coding: utf-8 -*-
"""外部工具连接 RFC · 增量: OpenAPI → HTTP 工具自动生成 (复用 Stage1 HttpToolProvider)。"""
import unittest

from agent_module.tools.external import (
    openapi_to_specs, build_providers_from_config, HttpToolProvider,
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


if __name__ == "__main__":
    unittest.main()
