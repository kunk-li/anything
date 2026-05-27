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


class TestToolDescriptions(unittest.TestCase):
    def test_all_4_tools_have_descriptions(self):
        for name in ("calculator", "datetime", "wikipedia", "document_read"):
            self.assertIn(name, TOOL_DESCRIPTIONS)
            self.assertGreater(len(TOOL_DESCRIPTIONS[name]), 20)


if __name__ == "__main__":
    unittest.main()
