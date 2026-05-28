# -*- coding: utf-8 -*-
"""
JsonFormatter 单测 (Task UU #81).
"""
import json
import logging
import unittest
from io import StringIO

from log_module.utils import JsonFormatter, use_json_format


class TestJsonFormatter(unittest.TestCase):
    def setUp(self):
        self.buf = StringIO()
        self.handler = logging.StreamHandler(self.buf)
        self.handler.setFormatter(JsonFormatter())

        # 每个测试用独立 logger 避免相互污染
        self.logger = logging.getLogger(f"test_{id(self)}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers = [self.handler]
        self.logger.propagate = False

    def _log_lines(self):
        out = self.buf.getvalue().strip()
        if not out:
            return []
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def test_simple_info(self):
        self.logger.info("hello world")
        lines = self._log_lines()
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertEqual(rec["level"], "INFO")
        self.assertEqual(rec["message"], "hello world")
        self.assertIn("ts", rec)
        self.assertIn("pid", rec)
        # ts 是 ISO 8601 — 包含 'T' 分隔符 + 时区
        self.assertIn("T", rec["ts"])

    def test_level_names(self):
        self.logger.debug("d"); self.logger.info("i")
        self.logger.warning("w"); self.logger.error("e")
        lines = self._log_lines()
        self.assertEqual([r["level"] for r in lines], ["DEBUG", "INFO", "WARNING", "ERROR"])

    def test_extras_serialized(self):
        self.logger.info("with extra", extra={
            "trace_id": "abc-123",
            "tenant_id": "default",
            "tokens": 42,
        })
        lines = self._log_lines()
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertEqual(rec["trace_id"], "abc-123")
        self.assertEqual(rec["tenant_id"], "default")
        self.assertEqual(rec["tokens"], 42)

    def test_exception_info(self):
        try:
            raise ValueError("boom")
        except ValueError:
            self.logger.exception("caught")
        lines = self._log_lines()
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertEqual(rec["message"], "caught")
        self.assertIn("exc_info", rec)
        self.assertIn("ValueError", rec["exc_info"])
        self.assertIn("boom", rec["exc_info"])

    def test_non_serializable_extra_fallback_to_str(self):
        class Custom:
            def __repr__(self):
                return "<Custom obj>"
        self.logger.info("with custom obj", extra={"obj": Custom()})
        lines = self._log_lines()
        rec = lines[0]
        # 非 JSON 类型 fallback 到 str()
        self.assertIn("Custom", rec["obj"])

    def test_chinese_message_not_escaped(self):
        self.logger.info("你好, world")
        lines = self._log_lines()
        # ensure_ascii=False 让中文原样输出 (单行 JSON 仍然合法, 因为 json.loads 也能解)
        self.assertEqual(lines[0]["message"], "你好, world")

    def test_default_kwargs(self):
        """JsonFormatter(default_kwargs={...}) 允许注入常量字段, 如 service name."""
        self.handler.setFormatter(JsonFormatter(default_kwargs={"service": "anything", "env": "test"}))
        self.logger.info("hi")
        rec = self._log_lines()[0]
        self.assertEqual(rec["service"], "anything")
        self.assertEqual(rec["env"], "test")


class TestUseJsonFormat(unittest.TestCase):
    def test_default_false(self):
        import os
        old = os.environ.pop("ANYTHING_LOG_FORMAT", None)
        try:
            self.assertFalse(use_json_format())
        finally:
            if old is not None:
                os.environ["ANYTHING_LOG_FORMAT"] = old

    def test_json_value_true(self):
        import os
        old = os.environ.get("ANYTHING_LOG_FORMAT")
        try:
            for v in ("json", "JSON", "1", "true", "yes"):
                os.environ["ANYTHING_LOG_FORMAT"] = v
                self.assertTrue(use_json_format(), f"value {v!r} should enable JSON")
            os.environ["ANYTHING_LOG_FORMAT"] = "plain"
            self.assertFalse(use_json_format())
            os.environ["ANYTHING_LOG_FORMAT"] = ""
            self.assertFalse(use_json_format())
        finally:
            if old is not None:
                os.environ["ANYTHING_LOG_FORMAT"] = old
            elif "ANYTHING_LOG_FORMAT" in os.environ:
                del os.environ["ANYTHING_LOG_FORMAT"]


if __name__ == "__main__":
    unittest.main()
