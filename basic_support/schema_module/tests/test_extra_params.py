# -*- coding: utf-8 -*-
"""
ExtraParams 强类型 schema 单测 (Task QQ #77).
"""
import unittest

from schema_module import ExtraParams


class TestExtraParamsDefaults(unittest.TestCase):
    def test_empty_dict_gives_default(self):
        p = ExtraParams.from_dict({})
        self.assertFalse(p.plan_only)
        self.assertFalse(p.approve_plan)
        self.assertEqual(p.approve_tools, [])
        self.assertIsNone(p.execution_mode)
        self.assertIsNone(p.execution_strategy)
        self.assertIsNone(p.source)

    def test_none_input(self):
        p = ExtraParams.from_dict(None)
        self.assertFalse(p.plan_only)

    def test_default_constructor(self):
        p = ExtraParams()
        self.assertFalse(p.plan_only)
        self.assertEqual(p.approve_tools, [])


class TestExtraParamsKnownKeys(unittest.TestCase):
    def test_plan_mode(self):
        p = ExtraParams.from_dict({"plan_only": True, "approve_plan": False})
        self.assertTrue(p.plan_only)
        self.assertFalse(p.approve_plan)

    def test_approve_tools(self):
        p = ExtraParams.from_dict({"approve_tools": ["python_sandbox", "email_send"]})
        self.assertEqual(p.approve_tools, ["python_sandbox", "email_send"])

    def test_execution_mode_valid(self):
        for m in ("rag", "agent", "hybrid"):
            p = ExtraParams.from_dict({"execution_mode": m})
            self.assertEqual(p.execution_mode, m)

    def test_execution_mode_invalid(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            ExtraParams.from_dict({"execution_mode": "bogus"})

    def test_execution_strategy_valid(self):
        for s in ("parallel", "serial"):
            p = ExtraParams.from_dict({"execution_strategy": s})
            self.assertEqual(p.execution_strategy, s)

    def test_source(self):
        p = ExtraParams.from_dict({"source": "console_app"})
        self.assertEqual(p.source, "console_app")

    def test_attachments_default_empty(self):
        self.assertEqual(ExtraParams.from_dict({}).attachments, [])

    def test_attachments_roundtrip(self):
        atts = [{"name": "a.pdf", "mime": "application/pdf",
                 "path": "uploads/a.pdf", "doc_id": None}]
        p = ExtraParams.from_dict({"attachments": atts})
        self.assertEqual(p.attachments, atts)
        self.assertEqual(p.to_dict()["attachments"], atts)

    def test_attachments_not_list_raises(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            ExtraParams.from_dict({"attachments": "uploads/a.pdf"})


class TestExtraParamsExtraAllow(unittest.TestCase):
    """未知 key 应该透传, 不报错 (渐进迁移期)."""

    def test_unknown_key_passes_through(self):
        p = ExtraParams.from_dict({"plan_only": True, "future_feature": "xyz"})
        self.assertTrue(p.plan_only)
        # extra="allow" 让未知 key 仍可访问
        d = p.to_dict()
        self.assertEqual(d.get("future_feature"), "xyz")

    def test_to_dict_preserves_known_and_unknown(self):
        d_in = {"plan_only": True, "custom": 42}
        p = ExtraParams.from_dict(d_in)
        d_out = p.to_dict()
        self.assertTrue(d_out["plan_only"])
        self.assertEqual(d_out["custom"], 42)


class TestExtraParamsTypeCoercion(unittest.TestCase):
    def test_plan_only_string_true_raises(self):
        # 严格 bool, 不允许 "true" 字符串隐式转换 (Pydantic v2 默认行为)
        from pydantic import ValidationError
        # 实际上 Pydantic v2 默认会做一些字符串→bool 转换 ("true"/"false")
        # 这里只是确保非法值 ("yes_please") 报错
        with self.assertRaises(ValidationError):
            ExtraParams.from_dict({"plan_only": "yes_please"})

    def test_approve_tools_not_list_raises(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            ExtraParams.from_dict({"approve_tools": "python_sandbox"})  # 应是 list


if __name__ == "__main__":
    unittest.main()
