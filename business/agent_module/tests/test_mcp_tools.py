# -*- coding: utf-8 -*-
"""外部工具连接 RFC · Stage2: MCP 客户端 (最小自实现 stdio JSON-RPC, 不起真子进程)。

验证: McpStdioClient 协议(fake transport: initialize/tools-list/tools-call/error) +
McpToolProvider(fake client: 命名空间/success&error envelope/连不上 fail-safe/disabled 过滤) +
build_providers_from_config 含 mcp_servers。
"""
import json
import unittest

from agent_module.tools.external import (
    McpServerSpec, McpStdioClient, McpHttpClient, McpToolProvider, build_providers_from_config,
)
from agent_module.tools.external.mcp_provider import _default_client_for


class _FakeTransport:
    """模拟 MCP server stdio: 按 method 回预设 result; 通知(无 id)不回。"""
    def __init__(self, responses, error_methods=()):
        self.responses = responses
        self.error_methods = set(error_methods)
        self._queue = []
        self.sent = []

    def write(self, line):
        self.sent.append(line)
        msg = json.loads(line)
        if "id" not in msg:
            return  # notification → 无响应
        if msg["method"] in self.error_methods:
            self._queue.append(json.dumps(
                {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -1, "message": "boom"}}) + "\n")
        else:
            self._queue.append(json.dumps(
                {"jsonrpc": "2.0", "id": msg["id"], "result": self.responses.get(msg["method"], {})}) + "\n")

    def readline(self):
        return self._queue.pop(0) if self._queue else ""


class TestMcpStdioClient(unittest.TestCase):
    def test_list_tools(self):
        t = _FakeTransport({
            "initialize": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "fake"}},
            "tools/list": {"tools": [{"name": "echo", "description": "echo", "inputSchema": {"type": "object"}}]},
        })
        c = McpStdioClient(McpServerSpec(name="fake", command="x"), transport=t)
        tools = c.list_tools()
        self.assertEqual(tools[0]["name"], "echo")
        # initialize 握手 + initialized 通知 + tools/list 都发了
        methods = [json.loads(s)["method"] for s in t.sent]
        self.assertIn("initialize", methods)
        self.assertIn("notifications/initialized", methods)
        self.assertIn("tools/list", methods)

    def test_call_tool(self):
        t = _FakeTransport({
            "initialize": {},
            "tools/call": {"content": [{"type": "text", "text": "hello"}], "isError": False},
        })
        c = McpStdioClient(McpServerSpec(name="fake", command="x"), transport=t)
        r = c.call_tool("echo", {"msg": "hi"})
        self.assertEqual(r["content"][0]["text"], "hello")

    def test_error_response_raises(self):
        t = _FakeTransport({"initialize": {}}, error_methods=("tools/list",))
        c = McpStdioClient(McpServerSpec(name="f", command="x"), transport=t)
        with self.assertRaises(RuntimeError):
            c.list_tools()

    def test_server_closed_raises(self):
        class _Empty:
            def write(self, line): pass
            def readline(self): return ""    # 永远空 = server 关闭
        c = McpStdioClient(McpServerSpec(name="f", command="x"), transport=_Empty())
        with self.assertRaises(RuntimeError):
            c.initialize()


class _FakeClient:
    def __init__(self, tools, call_result=None, fail=False):
        self._tools, self._call_result, self._fail = tools, call_result, fail
    def list_tools(self):
        if self._fail:
            raise RuntimeError("connect fail")
        return self._tools
    def call_tool(self, name, args):
        return self._call_result


class TestMcpToolProvider(unittest.TestCase):
    def _provider(self, fc, name="srv", enabled=True):
        return McpToolProvider([McpServerSpec(name=name, command="x", enabled=enabled)],
                               client_factory=lambda spec: fc)

    def test_discover_namespacing(self):
        defs = self._provider(_FakeClient([{"name": "search", "description": "d", "inputSchema": {}}])).discover()
        self.assertEqual(defs[0].name, "srv.search")     # server.tool 命名空间
        self.assertTrue(defs[0].requires_approval)       # 默认需审批
        self.assertEqual(defs[0].source, "mcp:srv")
        self.assertEqual(defs[0].input_schema, {})

    def test_tool_call_success_envelope(self):
        fc = _FakeClient([{"name": "echo"}],
                         call_result={"content": [{"type": "text", "text": "ok!"}], "isError": False})
        r = self._provider(fc).discover()[0].func({"x": 1})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["result"], "ok!")

    def test_tool_call_error_envelope(self):
        fc = _FakeClient([{"name": "e"}],
                         call_result={"content": [{"type": "text", "text": "bad"}], "isError": True})
        r = self._provider(fc).discover()[0].func({})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")

    def test_server_connect_fail_skipped(self):
        self.assertEqual(self._provider(_FakeClient([], fail=True)).discover(), [])   # fail-safe

    def test_disabled_server_filtered(self):
        self.assertEqual(self._provider(_FakeClient([{"name": "t"}]), enabled=False).specs, [])


class TestBuildMcpFromConfig(unittest.TestCase):
    def test_build_includes_mcp_provider(self):
        class _Cfg:
            def get_config(self, k, default=None):
                if k == "agent.mcp_servers":
                    return [{"name": "fs", "command": "npx", "args": ["-y", "srv"], "junk": 1}]
                return default
        providers = build_providers_from_config(_Cfg())
        self.assertEqual(len(providers), 1)
        self.assertIsInstance(providers[0], McpToolProvider)
        self.assertEqual(providers[0].specs[0].name, "fs")
        self.assertEqual(providers[0].specs[0].args, ["-y", "srv"])   # junk 被过滤, args 保留


class _FakeHttpPost:
    """模拟 MCP HTTP server: 按 method 回预设 result (json 或 sse); 可带 session id; 记录调用。"""
    def __init__(self, responses, session_id=None, sse=False):
        self.responses, self.session_id, self.sse = responses, session_id, sse
        self.calls = []

    def __call__(self, url, payload, headers):
        self.calls.append({"url": url, "payload": payload, "headers": dict(headers)})
        method, rid = payload.get("method"), payload.get("id")
        resp_headers = {}
        if self.session_id and method == "initialize":
            resp_headers["Mcp-Session-Id"] = self.session_id
        if rid is None:
            return 202, {}, ""                       # notification
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "result": self.responses.get(method, {})})
        if self.sse:
            body = f"event: message\ndata: {body}\n\n"
        return 200, resp_headers, body


class TestMcpHttpClient(unittest.TestCase):
    def _spec(self):
        return McpServerSpec(name="r", transport="http", url="https://1.1.1.1/mcp")

    def test_list_tools_json_response(self):
        post = _FakeHttpPost({"initialize": {}, "tools/list": {"tools": [{"name": "remote_echo"}]}})
        c = McpHttpClient(self._spec(), post=post)
        self.assertEqual(c.list_tools()[0]["name"], "remote_echo")

    def test_call_tool_sse_response(self):
        post = _FakeHttpPost({"initialize": {}, "tools/call": {"content": [{"type": "text", "text": "hi"}]}}, sse=True)
        c = McpHttpClient(self._spec(), post=post)
        r = c.call_tool("x", {})
        self.assertEqual(r["content"][0]["text"], "hi")

    def test_session_id_captured_and_reused(self):
        post = _FakeHttpPost({"initialize": {}, "tools/list": {"tools": []}}, session_id="sess-123")
        McpHttpClient(self._spec(), post=post).list_tools()
        tl = [c for c in post.calls if c["payload"].get("method") == "tools/list"][0]
        self.assertEqual(tl["headers"].get("Mcp-Session-Id"), "sess-123")   # 后续请求带 session id

    def test_error_response_raises(self):
        class _ErrPost:
            def __call__(self, url, payload, headers):
                rid = payload.get("id")
                if rid is None:
                    return 202, {}, ""
                return 200, {}, json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -1, "message": "x"}})
        with self.assertRaises(RuntimeError):
            McpHttpClient(self._spec(), post=_ErrPost()).list_tools()


class TestTransportRouting(unittest.TestCase):
    def test_default_client_for_http(self):
        self.assertIsInstance(
            _default_client_for(McpServerSpec(name="r", transport="http", url="https://x/mcp")), McpHttpClient)

    def test_default_client_for_stdio(self):
        self.assertIsInstance(
            _default_client_for(McpServerSpec(name="r", command="x")), McpStdioClient)


if __name__ == "__main__":
    unittest.main()
