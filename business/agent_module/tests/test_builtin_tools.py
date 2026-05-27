# -*- coding: utf-8 -*-
"""
单测 — Task #37 扩展工具集 (calculator / datetime / wikipedia / document_read).
"""

import os

# 避免 build_basic_deps secrets fail-fast
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import unittest
from unittest.mock import MagicMock, patch

from agent_module.tools import (
    calculator_tool,
    datetime_tool,
    wikipedia_tool,
    make_document_read_tool,
    regex_extract,
    text_stats,
    json_query,
    http_get,
    make_text_summarize_tool,
    TOOL_DESCRIPTIONS,
)


class TestCalculatorTool(unittest.TestCase):

    def test_basic_arith(self):
        r = calculator_tool({"expression": "1+2*3"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["result"], 7)

    def test_powers_and_negatives(self):
        r = calculator_tool({"expression": "-(2**3) + 4"})
        self.assertEqual(r["data"]["result"], -4)

    def test_funcs_and_constants(self):
        r = calculator_tool({"expression": "sqrt(9) + pi"})
        self.assertEqual(r["code"], "SUCCESS")
        # sqrt(9)=3.0 + pi=3.14159... -> ~6.14
        self.assertAlmostEqual(r["data"]["result"], 3.0 + 3.141592653589793, places=4)

    def test_float_div(self):
        r = calculator_tool({"expression": "10/3"})
        self.assertAlmostEqual(r["data"]["result"], 10 / 3)

    def test_max_min_sum(self):
        r = calculator_tool({"expression": "max(1, 2, 3) + min(7, 4)"})
        self.assertEqual(r["data"]["result"], 7)

    def test_reject_attribute_access(self):
        """属性/import 之类的注入应该被拒"""
        r = calculator_tool({"expression": "__import__('os').system('echo hi')"})
        self.assertNotEqual(r["code"], "SUCCESS")

    def test_reject_unknown_name(self):
        r = calculator_tool({"expression": "x + 1"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")
        self.assertIn("未知标识符", r["message"])

    def test_reject_string(self):
        r = calculator_tool({"expression": '"hello" + "world"'})
        self.assertNotEqual(r["code"], "SUCCESS")

    def test_empty(self):
        r = calculator_tool({"expression": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_too_long(self):
        r = calculator_tool({"expression": "1+" * 200})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_syntax_error(self):
        r = calculator_tool({"expression": "1 +"})
        self.assertEqual(r["code"], "PARAM_INVALID")
        self.assertIn("语法错误", r["message"])


class TestDatetimeTool(unittest.TestCase):

    def test_now_default_utc(self):
        r = datetime_tool({"op": "now"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertIn("iso", r["data"])
        self.assertIn("timestamp", r["data"])
        self.assertEqual(r["data"]["tz_offset_hours"], 0)

    def test_now_with_tz(self):
        r = datetime_tool({"op": "now", "tz_offset_hours": 8})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["tz_offset_hours"], 8)
        self.assertIn("+08:00", r["data"]["iso"])

    def test_add_days(self):
        r = datetime_tool({"op": "add", "iso": "2026-05-27T10:00:00", "days": 7})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["iso"], "2026-06-03T10:00:00")

    def test_diff(self):
        r = datetime_tool({
            "op": "diff",
            "iso_start": "2026-01-01T00:00:00",
            "iso_end": "2026-01-08T00:00:00",
        })
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["days"], 7)
        self.assertEqual(r["data"]["seconds"], 7 * 86400)

    def test_invalid_op(self):
        r = datetime_tool({"op": "rocket-launch"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_add_missing_iso(self):
        r = datetime_tool({"op": "add"})
        self.assertEqual(r["code"], "PARAM_MISSING")


class TestWikipediaTool(unittest.TestCase):
    """离线测试 — 用 patch 替换 urlopen, 模拟成功 / 网络失败两种路径"""

    def test_query_empty(self):
        r = wikipedia_tool({"query": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_offline_returns_failure(self):
        """模拟 urlopen 抛网络异常"""
        with patch("agent_module.tools.builtin_tools.urllib.request.urlopen",
                   side_effect=ConnectionError("offline")):
            r = wikipedia_tool({"query": "Anthropic"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")
        self.assertIn("wikipedia", r["message"].lower())

    def test_no_results(self):
        """opensearch 返回空 titles 列表"""
        from io import BytesIO
        fake_resp = MagicMock()
        fake_resp.__enter__ = lambda s: fake_resp
        fake_resp.__exit__ = lambda *a: None
        fake_resp.read.return_value = b'["xyz", [], [], []]'
        with patch("agent_module.tools.builtin_tools.urllib.request.urlopen",
                   return_value=fake_resp):
            r = wikipedia_tool({"query": "xyzzzz-no-match"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertIsNone(r["data"]["title"])
        self.assertEqual(r["data"]["summary"], "")


class TestDocumentReadTool(unittest.TestCase):

    def test_missing_doc_id(self):
        tool = make_document_read_tool(lambda tid: MagicMock())
        r = tool({})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_doc_not_found(self):
        class _EmptyStore:
            def get_document(self, doc_id): return None
        tool = make_document_read_tool(lambda tid: _EmptyStore())
        r = tool({"doc_id": "missing-id"})
        self.assertEqual(r["code"], "DOCUMENT_NOT_FOUND")

    def test_doc_returned(self):
        class _Store:
            def get_document(self, doc_id):
                return {"content": "x" * 5000, "file_name": "a.md", "file_type": "md"}
        tool = make_document_read_tool(lambda tid: _Store())
        r = tool({"doc_id": "abc", "max_chars": 100})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["total_chars"], 5000)
        self.assertEqual(len(r["data"]["content"]), 100)
        self.assertTrue(r["data"]["truncated"])
        self.assertEqual(r["data"]["file_name"], "a.md")

    def test_invalid_doc_id_raises(self):
        class _Store:
            def get_document(self, doc_id):
                raise ValueError("doc_id 格式非法")
        tool = make_document_read_tool(lambda tid: _Store())
        r = tool({"doc_id": "not-uuid"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_factory_failure(self):
        def _bad_factory(tid):
            raise RuntimeError("不能创建 doc_store")
        tool = make_document_read_tool(_bad_factory)
        r = tool({"doc_id": "any"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")


class TestRegexExtract(unittest.TestCase):

    def test_simple_match(self):
        r = regex_extract({
            "text": "Contact: alice@example.com or bob@test.org",
            "pattern": r"[\w.]+@[\w.]+",
        })
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["match_count"], 2)
        self.assertEqual(r["data"]["matches"][0]["match"], "alice@example.com")

    def test_with_groups(self):
        r = regex_extract({
            "text": "phone: 010-12345678, mobile: 138-0000-1234",
            "pattern": r"(\d{3,4})-(\d{4,8})(?:-(\d{4}))?",
        })
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["matches"][0]["groups"][0], "010")

    def test_named_groups(self):
        r = regex_extract({
            "text": "ver 1.2.3 released",
            "pattern": r"(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)",
        })
        self.assertEqual(r["data"]["matches"][0]["named"], {
            "major": "1", "minor": "2", "patch": "3",
        })

    def test_flags_ignorecase(self):
        r = regex_extract({"text": "Hello HELLO hello", "pattern": "hello", "flags": "i"})
        self.assertEqual(r["data"]["match_count"], 3)

    def test_max_matches_truncates(self):
        r = regex_extract({"text": "x" * 100, "pattern": "x", "max_matches": 5})
        self.assertEqual(r["data"]["match_count"], 5)
        self.assertTrue(r["data"]["truncated"])

    def test_invalid_pattern(self):
        r = regex_extract({"text": "abc", "pattern": "("})
        self.assertEqual(r["code"], "PARAM_INVALID")
        self.assertIn("正则编译失败", r["message"])

    def test_empty_text(self):
        r = regex_extract({"text": "", "pattern": "x"})
        self.assertEqual(r["code"], "PARAM_MISSING")


class TestTextStats(unittest.TestCase):

    def test_basic(self):
        r = text_stats({"text": "Hello 你好\nWorld 世界"})
        self.assertEqual(r["code"], "SUCCESS")
        d = r["data"]
        self.assertEqual(d["line_count"], 2)
        self.assertEqual(d["cjk_chars"], 4)  # 你好世界
        self.assertGreater(d["ascii_chars"], 0)

    def test_digits_and_no_space(self):
        r = text_stats({"text": "abc 123\n456"})
        d = r["data"]
        self.assertEqual(d["digit_chars"], 6)  # 1 2 3 4 5 6
        self.assertEqual(d["char_count_no_space"], 9)  # abc123456

    def test_not_string(self):
        r = text_stats({"text": 123})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_too_long(self):
        r = text_stats({"text": "x" * 2_000_000})
        self.assertEqual(r["code"], "PARAM_INVALID")


class TestJsonQuery(unittest.TestCase):

    def test_simple_path(self):
        r = json_query({"data": {"user": {"name": "Alice", "age": 30}}, "path": "user.name"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["result"], "Alice")

    def test_array_index(self):
        r = json_query({"data": {"items": [{"title": "a"}, {"title": "b"}]}, "path": "items.[1].title"})
        self.assertEqual(r["data"]["result"], "b")

    def test_negative_index(self):
        r = json_query({"data": {"items": [10, 20, 30]}, "path": "items.[-1]"})
        self.assertEqual(r["data"]["result"], 30)

    def test_wildcard(self):
        r = json_query({"data": {"items": [{"v": 1}, {"v": 2}, {"v": 3}]}, "path": "items.*.v"})
        self.assertEqual(r["data"]["result"], [1, 2, 3])

    def test_json_text(self):
        r = json_query({"json_text": '{"a": [1, 2, 3]}', "path": "a.[2]"})
        self.assertEqual(r["data"]["result"], 3)

    def test_key_not_found(self):
        r = json_query({"data": {"a": 1}, "path": "b"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")

    def test_index_out_of_range(self):
        r = json_query({"data": {"items": [1, 2]}, "path": "items.[10]"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")

    def test_invalid_json_text(self):
        r = json_query({"json_text": "{not valid", "path": "a"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_neither_data_nor_text(self):
        r = json_query({"path": "x"})
        self.assertEqual(r["code"], "PARAM_MISSING")


class TestHttpGet(unittest.TestCase):

    def test_reject_non_http_scheme(self):
        r = http_get({"url": "file:///etc/passwd"})
        self.assertEqual(r["code"], "PARAM_INVALID")
        self.assertIn("http / https", r["message"])

    def test_reject_private_ip_literal(self):
        for ip in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.1.1"):
            r = http_get({"url": f"http://{ip}/x"})
            self.assertEqual(r["code"], "PARAM_INVALID", ip)
            self.assertIn("SSRF", r["message"])

    def test_reject_loopback_ipv6(self):
        r = http_get({"url": "http://[::1]/x"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_dns_resolution_failure(self):
        """不存在的域名 -> PARAM_INVALID (DNS 失败也算 SSRF 防御)"""
        r = http_get({"url": "http://this-domain-definitely-does-not-exist-xxxyyy.invalid/path"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_empty_url(self):
        r = http_get({"url": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")


class TestTextSummarize(unittest.TestCase):

    def test_calls_llm(self):
        called = {}
        def _fake_llm(prompt):
            called["prompt"] = prompt
            return "这是压缩后的 3 句摘要。第二句。第三句。"
        tool = make_text_summarize_tool(_fake_llm)
        r = tool({"text": "很长很长很长" * 100, "max_sentences": 3})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertIn("压缩成 3 句话", called["prompt"])
        self.assertEqual(r["data"]["max_sentences"], 3)

    def test_lang_english(self):
        called = {}
        def _fake_llm(prompt):
            called["prompt"] = prompt
            return "summary."
        tool = make_text_summarize_tool(_fake_llm)
        tool({"text": "lorem", "lang": "en"})
        self.assertIn("Summarize the following", called["prompt"])

    def test_empty_text(self):
        tool = make_text_summarize_tool(lambda p: "x")
        r = tool({"text": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_llm_failure(self):
        def _bad(p): raise RuntimeError("LLM down")
        tool = make_text_summarize_tool(_bad)
        r = tool({"text": "some content"})
        self.assertEqual(r["code"], "TOOL_CALL_FAILED")
        self.assertTrue(r["retryable"])


class TestToolDescriptions(unittest.TestCase):
    def test_all_9_tools_have_descriptions(self):
        expected = (
            "calculator", "datetime", "wikipedia", "document_read",
            "regex_extract", "text_stats", "json_query", "http_get", "text_summarize",
        )
        for name in expected:
            self.assertIn(name, TOOL_DESCRIPTIONS, name)
            self.assertGreater(len(TOOL_DESCRIPTIONS[name]), 20)


if __name__ == "__main__":
    unittest.main()
