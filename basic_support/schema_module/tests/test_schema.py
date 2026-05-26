# -*- coding: utf-8 -*-
"""
schema_module 单元测试
"""

import unittest

from schema_module import (
    RequestEnvelope,
    ResponseEnvelope,
    validate_request_dict,
)


class TestRequestEnvelope(unittest.TestCase):
    def test_valid_rag_request(self):
        env = RequestEnvelope.model_validate({
            "type": "rag",
            "query": "什么是 RAG？",
            "top_k": 3,
            "trace_id": "t1",
        })
        self.assertEqual(env.type, "rag")
        self.assertEqual(env.query, "什么是 RAG？")
        self.assertEqual(env.top_k, 3)

    def test_valid_agent_request(self):
        env = RequestEnvelope.model_validate({
            "type": "agent",
            "task": "写计划",
        })
        self.assertEqual(env.type, "agent")
        self.assertEqual(env.task, "写计划")
        self.assertEqual(env.top_k, 5)  # default

    def test_default_type_is_rag(self):
        env = RequestEnvelope.model_validate({"query": "abc"})
        self.assertEqual(env.type, "rag")

    def test_extra_fields_ignored(self):
        env = RequestEnvelope.model_validate({
            "type": "rag",
            "query": "abc",
            "unknown_field": "should be ignored",
        })
        self.assertFalse(hasattr(env, "unknown_field"))

    def test_empty_string_query_treated_as_missing(self):
        ok, msg, code = validate_request_dict({"type": "rag", "query": "   "})
        self.assertFalse(ok)
        self.assertEqual(code, "PARAM_MISSING")


class TestValidateRequestDict(unittest.TestCase):
    def test_success(self):
        ok, msg, code = validate_request_dict({"type": "rag", "query": "abc"})
        self.assertTrue(ok)
        self.assertEqual(code, "SUCCESS")

    def test_bad_type(self):
        ok, msg, code = validate_request_dict({"type": "unknown", "query": "abc"})
        self.assertFalse(ok)
        self.assertEqual(code, "BAD_REQUEST")

    def test_top_k_out_of_range(self):
        ok, msg, code = validate_request_dict({"type": "rag", "query": "abc", "top_k": 100})
        self.assertFalse(ok)
        self.assertEqual(code, "PARAM_INVALID")

    def test_top_k_wrong_type(self):
        ok, msg, code = validate_request_dict({"type": "rag", "query": "abc", "top_k": "abc"})
        self.assertFalse(ok)
        self.assertEqual(code, "PARAM_INVALID")

    def test_missing_query_for_rag(self):
        ok, msg, code = validate_request_dict({"type": "rag"})
        self.assertFalse(ok)
        self.assertEqual(code, "PARAM_MISSING")

    def test_missing_task_for_agent(self):
        ok, msg, code = validate_request_dict({"type": "agent"})
        self.assertFalse(ok)
        self.assertEqual(code, "PARAM_MISSING")

    def test_missing_task_for_hybrid(self):
        ok, msg, code = validate_request_dict({"type": "hybrid"})
        self.assertFalse(ok)
        self.assertEqual(code, "PARAM_MISSING")


class TestResponseEnvelope(unittest.TestCase):
    def test_default_success(self):
        env = ResponseEnvelope()
        self.assertEqual(env.code, "SUCCESS")
        self.assertEqual(env.message, "ok")
        self.assertFalse(env.retryable)
        self.assertIsNone(env.data)

    def test_failure_envelope(self):
        env = ResponseEnvelope(
            code="RAG_RUN_FAILED",
            message="rag 执行失败",
            retryable=True,
            trace_id="t1",
            details={"stage": "rag"},
            cost_time=1.23,
        )
        self.assertEqual(env.code, "RAG_RUN_FAILED")
        self.assertTrue(env.retryable)
        self.assertEqual(env.trace_id, "t1")


if __name__ == "__main__":
    unittest.main()
