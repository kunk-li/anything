# -*- coding: utf-8 -*-
"""外部工具连接 RFC · Stage1: HTTP/OpenAPI 连接器。

验证: spec→可用工具(注入 fake fetch) / 占位填充+query / SSRF 拒私网+非http /
JSON body / response_path 抽取 / fetch 异常 envelope / provider discover /
register_external_tools 注册+审批 / build_providers_from_config(过滤未知键)。
"""
import json
import unittest

from agent_module.tools.external import (
    HttpToolSpec, HttpToolProvider, make_http_tool,
    McpToolProvider, McpServerSpec,
    register_external_tools, build_providers_from_config,
)


class _FakeFetch:
    """注入的假 fetch: 记录调用, 返回固定 (status, text)。不发真实网络。"""
    def __init__(self, status=200, text='{"data": {"answer": "hi"}}'):
        self.status, self.text = status, text
        self.calls = []

    def __call__(self, method, url, headers, body, timeout, max_bytes):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return self.status, self.text


class TestMakeHttpTool(unittest.TestCase):
    def test_get_extract_response_path(self):
        f = _FakeFetch(text='{"data": {"answer": "hi"}}')
        tool = make_http_tool(
            HttpToolSpec(name="t", url="https://1.1.1.1/x", response_path="data.answer"), fetch=f)
        r = tool({})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["result"], "hi")
        self.assertEqual(f.calls[0]["method"], "GET")

    def test_placeholder_and_query(self):
        f = _FakeFetch(text="{}")
        tool = make_http_tool(
            HttpToolSpec(name="t", url="https://1.1.1.1/u/{uid}", query=["q"]), fetch=f)
        tool({"uid": "42", "q": "hello"})
        url = f.calls[0]["url"]
        self.assertIn("/u/42", url)
        self.assertIn("q=hello", url)

    def test_ssrf_private_ip_rejected_no_fetch(self):
        f = _FakeFetch()
        tool = make_http_tool(HttpToolSpec(name="t", url="http://127.0.0.1/x"), fetch=f)
        r = tool({})
        self.assertEqual(r["code"], "PARAM_INVALID")
        self.assertIn("SSRF", r["message"])
        self.assertEqual(len(f.calls), 0)            # 没真发请求

    def test_non_http_scheme_rejected(self):
        r = make_http_tool(HttpToolSpec(name="t", url="file:///etc/passwd"), fetch=_FakeFetch())({})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_post_json_body(self):
        f = _FakeFetch(text='{"ok": true}')
        tool = make_http_tool(
            HttpToolSpec(name="t", url="https://1.1.1.1/p", method="POST", body=["msg"]), fetch=f)
        tool({"msg": "hi", "ignored": 1})
        self.assertEqual(f.calls[0]["method"], "POST")
        self.assertEqual(json.loads(f.calls[0]["body"]), {"msg": "hi"})   # 仅 body 声明的键

    def test_missing_placeholder_param(self):
        r = make_http_tool(HttpToolSpec(name="t", url="https://1.1.1.1/u/{uid}"), fetch=_FakeFetch())({})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_fetch_exception_envelope(self):
        def boom(*a):
            raise RuntimeError("net down")
        r = make_http_tool(HttpToolSpec(name="t", url="https://1.1.1.1/x"), fetch=boom)({})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")
        self.assertTrue(r["retryable"])

    def test_non_json_response_passthrough(self):
        f = _FakeFetch(text="plain text body")
        r = make_http_tool(HttpToolSpec(name="t", url="https://1.1.1.1/x"), fetch=f)({})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["result"], "plain text body")

    def test_auth_header_injected(self):
        f = _FakeFetch(text="{}")
        tool = make_http_tool(HttpToolSpec(
            name="t", url="https://1.1.1.1/x",
            auth_header="Authorization", auth_value="Bearer xyz"), fetch=f)
        tool({})
        self.assertEqual(f.calls[0]["headers"].get("Authorization"), "Bearer xyz")


class TestProviderAndRegister(unittest.TestCase):
    def test_discover_defaults(self):
        defs = HttpToolProvider([
            HttpToolSpec(name="a", url="https://x.com/a"),
            HttpToolSpec(name="b", url="https://x.com/b"),
        ]).discover()
        self.assertEqual([d.name for d in defs], ["a", "b"])
        self.assertTrue(all(d.requires_approval for d in defs))   # 默认需审批
        self.assertTrue(all(d.source == "http" for d in defs))

    def test_register_into_registry_and_approval(self):
        class _Reg:
            def __init__(self): self.t = {}
            def register(self, n, f, description=""): self.t[n] = f
            def get(self, n): return self.t.get(n)
        reg, approval = _Reg(), set()
        p = HttpToolProvider([HttpToolSpec(name="ext1", url="https://x.com/a")])
        names = register_external_tools(reg, [p], approval_set=approval)
        self.assertIn("ext1", reg.t)               # 已注册进 registry
        self.assertTrue(callable(reg.t["ext1"]))
        self.assertIn("ext1", names)               # 返回需审批名
        self.assertIn("ext1", approval)            # 加入审批集

    def test_build_from_config_filters_unknown_keys(self):
        class _Cfg:
            def get_config(self, k, default=None):
                if k == "agent.external_tools":
                    return [{"name": "weather_ext", "url": "https://1.1.1.1/w",
                             "query": ["city"], "junk_key": 1}]   # junk_key 应被过滤
                return default
        providers = build_providers_from_config(_Cfg())
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0].discover()[0].name, "weather_ext")

    def test_build_from_config_empty(self):
        class _Cfg:
            def get_config(self, k, default=None): return default
        self.assertEqual(build_providers_from_config(_Cfg()), [])
        self.assertEqual(build_providers_from_config(None), [])

    def test_build_mcp_http_server_discoverable(self):
        # http transport MCP server: 必填是 url 而非 command —— 不应被静默丢弃 (死功能修复)
        class _Cfg:
            def get_config(self, k, default=None):
                if k == "agent.mcp_servers":
                    return [{"name": "remote", "transport": "http",
                             "url": "https://1.1.1.1/mcp", "junk_key": 1}]
                return default
        providers = build_providers_from_config(_Cfg())
        self.assertEqual(len(providers), 1)
        self.assertIsInstance(providers[0], McpToolProvider)
        specs = providers[0].specs
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, "remote")
        self.assertEqual(specs[0].transport, "http")
        self.assertEqual(specs[0].url, "https://1.1.1.1/mcp")

    def test_build_mcp_stdio_server_discoverable(self):
        # stdio transport (默认): 必填 command, 无 url
        class _Cfg:
            def get_config(self, k, default=None):
                if k == "agent.mcp_servers":
                    return [{"name": "local", "command": "npx", "args": ["mcp-server"]}]
                return default
        providers = build_providers_from_config(_Cfg())
        self.assertEqual(len(providers), 1)
        specs = providers[0].specs
        self.assertEqual(specs[0].name, "local")
        self.assertEqual(specs[0].transport, "stdio")
        self.assertEqual(specs[0].command, "npx")

    def test_build_mcp_missing_endpoint_dropped(self):
        # stdio 缺 command / http 缺 url 都应被过滤 (各自端点必填)
        class _Cfg:
            def get_config(self, k, default=None):
                if k == "agent.mcp_servers":
                    return [{"name": "no_cmd"},                                   # stdio 缺 command
                            {"name": "http_no_url", "transport": "http"}]         # http 缺 url
                return default
        self.assertEqual(build_providers_from_config(_Cfg()), [])


if __name__ == "__main__":
    unittest.main()
