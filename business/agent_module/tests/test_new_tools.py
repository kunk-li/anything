# -*- coding: utf-8 -*-
"""
Task XXXX-12 (#159): 5 个新工具单测.

涵盖: image_generate / pdf_read / excel_read / sql_query / browser_visit.

策略: 不依赖真网络 / 真文件 / 真浏览器 — 全部跑参数校验 + 安全边界
+ mock 外部依赖. 真集成测试单独走 nightly.
"""
import os
import unittest
from unittest.mock import patch, MagicMock

from agent_module.tools.tools_impl.image_generate import image_generate_tool
from agent_module.tools.tools_impl.pdf_excel_read import pdf_read, excel_read, _resolve_safe_path
from agent_module.tools.tools_impl.sql_query import sql_query
from agent_module.tools.tools_impl.browser_visit import browser_visit


# ---------- image_generate ----------

class TestImageGenerate(unittest.TestCase):
    def test_empty_prompt(self):
        r = image_generate_tool({"prompt": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_whitespace_prompt(self):
        r = image_generate_tool({"prompt": "   \n  "})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_no_api_key(self):
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}, clear=False):
            r = image_generate_tool({"prompt": "a cat"})
            self.assertEqual(r["code"], "SERVICE_UNAVAILABLE")
            self.assertIn("DASHSCOPE_API_KEY", r["message"])

    def test_response_shape_keys(self):
        # 跑空 prompt 也能验证返回 schema (code/message/data/trace_id 都在)
        r = image_generate_tool({"prompt": ""})
        for k in ("code", "message", "data", "trace_id", "retryable"):
            self.assertIn(k, r)


# ---------- pdf_read / excel_read 路径安全 ----------

class TestPdfExcelPathSafety(unittest.TestCase):
    def test_invalid_path_absolute_outside_cwd(self):
        # 绝对路径出沙盒
        if os.name == "nt":
            r = pdf_read({"file_path": "C:/Windows/notepad.exe"})
        else:
            r = pdf_read({"file_path": "/etc/passwd"})
        self.assertEqual(r["code"], "INVALID_PATH")

    def test_invalid_path_dot_dot_escape(self):
        r = pdf_read({"file_path": "../../etc/passwd"})
        self.assertEqual(r["code"], "INVALID_PATH")

    def test_invalid_path_wrong_prefix(self):
        # cwd 内但不在 uploads/ source_docs/ 等前缀
        r = pdf_read({"file_path": "random_folder/x.pdf"})
        self.assertEqual(r["code"], "INVALID_PATH")

    def test_empty_path(self):
        r = pdf_read({"file_path": ""})
        self.assertEqual(r["code"], "INVALID_PATH")

    def test_excel_empty_path(self):
        r = excel_read({"file_path": ""})
        self.assertEqual(r["code"], "INVALID_PATH")

    def test_resolve_safe_path_returns_none_for_invalid(self):
        self.assertIsNone(_resolve_safe_path(""))
        self.assertIsNone(_resolve_safe_path(None))
        self.assertIsNone(_resolve_safe_path("../escape"))


# ---------- sql_query 安全边界 ----------

class TestSqlQuerySecurity(unittest.TestCase):
    def test_empty_query(self):
        r = sql_query({"query": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_reject_insert(self):
        r = sql_query({"query": "INSERT INTO users VALUES (1, 'x')"})
        self.assertEqual(r["code"], "FORBIDDEN_STATEMENT")

    def test_reject_update(self):
        r = sql_query({"query": "UPDATE users SET name='x' WHERE id=1"})
        self.assertEqual(r["code"], "FORBIDDEN_STATEMENT")

    def test_reject_delete(self):
        r = sql_query({"query": "DELETE FROM users WHERE id=1"})
        self.assertEqual(r["code"], "FORBIDDEN_STATEMENT")

    def test_reject_drop(self):
        r = sql_query({"query": "DROP TABLE users"})
        self.assertEqual(r["code"], "FORBIDDEN_STATEMENT")

    def test_reject_select_with_inline_delete(self):
        # 双保险: SELECT 开头但藏 DELETE 也得拦
        r = sql_query({"query": "SELECT * FROM users; DELETE FROM users"})
        self.assertEqual(r["code"], "FORBIDDEN_STATEMENT")

    def test_select_demo_users_smoke(self):
        # 默认 in-memory sqlite 有 demo 表
        r = sql_query({"query": "SELECT * FROM users LIMIT 1"})
        # 不强求 SUCCESS (sqlalchemy 可能未装), 但只要没被安全检查拦, 就是 SUCCESS 或 EXECUTION_FAILED 而非 FORBIDDEN_STATEMENT
        self.assertNotEqual(r["code"], "FORBIDDEN_STATEMENT")


# ---------- browser_visit SSRF + 参数 ----------

class TestBrowserVisit(unittest.TestCase):
    def test_empty_url(self):
        r = browser_visit({"url": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_invalid_scheme_file(self):
        r = browser_visit({"url": "file:///etc/passwd"})
        self.assertEqual(r["code"], "INVALID_URL")

    def test_invalid_scheme_javascript(self):
        r = browser_visit({"url": "javascript:alert(1)"})
        self.assertEqual(r["code"], "INVALID_URL")

    def test_ssrf_localhost(self):
        r = browser_visit({"url": "http://localhost:8000/"})
        self.assertEqual(r["code"], "FORBIDDEN_HOST")

    def test_ssrf_127(self):
        r = browser_visit({"url": "http://127.0.0.1/"})
        self.assertEqual(r["code"], "FORBIDDEN_HOST")

    def test_ssrf_10_dot(self):
        r = browser_visit({"url": "http://10.0.0.1/"})
        self.assertEqual(r["code"], "FORBIDDEN_HOST")

    def test_ssrf_192_168(self):
        r = browser_visit({"url": "http://192.168.1.1/"})
        self.assertEqual(r["code"], "FORBIDDEN_HOST")

    def test_invalid_action(self):
        r = browser_visit({"url": "https://example.com", "action": "kaboom"})
        self.assertEqual(r["code"], "PARAM_INVALID")


if __name__ == "__main__":
    unittest.main()
