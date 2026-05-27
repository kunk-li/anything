# -*- coding: utf-8 -*-
"""
schema_module 单元测试
"""

import unittest

from pydantic import ValidationError

from schema_module import (
    RequestEnvelope,
    ResponseEnvelope,
    validate_request_dict,
    Chunk,
    ChunkMeta,
    RetrievedChunk,
    Citation,
    ToolStep,
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

    def test_tenant_id_optional_default_none(self):
        """不传 tenant_id 时 schema 接受为 None, 后续由 RequestHandler 补 default"""
        env = RequestEnvelope.model_validate({"type": "rag", "query": "x"})
        self.assertIsNone(env.tenant_id)

    def test_tenant_id_valid_formats(self):
        for tid in ("acme-corp", "tenant_a", "abc", "x" * 32, "tenant-001", "default"):
            env = RequestEnvelope.model_validate({
                "type": "rag", "query": "x", "tenant_id": tid,
            })
            self.assertEqual(env.tenant_id, tid)

    def test_tenant_id_empty_string_treated_as_none(self):
        env = RequestEnvelope.model_validate({
            "type": "rag", "query": "x", "tenant_id": "",
        })
        self.assertIsNone(env.tenant_id)

    def test_tenant_id_rejects_invalid_chars(self):
        from pydantic import ValidationError
        for bad in (
            "Acme Corp",       # 大写 + 空格
            "../../etc",       # path traversal 尝试
            "tenant.id",       # 不允许的点
            "tenant@a",        # 不允许的 @
            "ab",              # < 3 字符
            "x" * 33,          # > 32 字符
            123,               # 非字符串
            "tenant/a",        # path 分隔符
        ):
            with self.assertRaises(ValidationError, msg=f"should reject {bad!r}"):
                RequestEnvelope.model_validate({
                    "type": "rag", "query": "x", "tenant_id": bad,
                })

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

    def test_invalid_tenant_id_returns_param_invalid(self):
        ok, msg, code = validate_request_dict({
            "type": "rag", "query": "x", "tenant_id": "Bad Tenant!",
        })
        self.assertFalse(ok)
        self.assertEqual(code, "PARAM_INVALID")
        self.assertIn("tenant_id", msg)


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


class TestChunk(unittest.TestCase):
    def _valid_meta(self, **overrides):
        base = dict(
            file_name="f.md",
            source="local",
            chunk_index=1,
            start_char=0,
            end_char=100,
            token_count_est=25,
        )
        base.update(overrides)
        return base

    def test_valid_chunk(self):
        c = Chunk.model_validate({
            "doc_id": "d1",
            "chunk_id": "d1#c000001",
            "content": "hello world",
            "meta": self._valid_meta(),
        })
        self.assertEqual(c.doc_id, "d1")
        self.assertEqual(c.meta.chunk_index, 1)

    def test_chunk_rejects_empty_content(self):
        with self.assertRaises(ValidationError):
            Chunk.model_validate({
                "doc_id": "d1",
                "chunk_id": "d1#c000001",
                "content": "",
                "meta": self._valid_meta(),
            })

    def test_chunk_rejects_empty_doc_id(self):
        with self.assertRaises(ValidationError):
            Chunk.model_validate({
                "doc_id": "",
                "chunk_id": "d1#c000001",
                "content": "x",
                "meta": self._valid_meta(),
            })

    def test_chunk_rejects_negative_chunk_index(self):
        with self.assertRaises(ValidationError):
            Chunk.model_validate({
                "doc_id": "d1",
                "chunk_id": "d1#c000001",
                "content": "x",
                "meta": self._valid_meta(chunk_index=-1),
            })

    def test_chunk_meta_allows_extras(self):
        """meta 允许扩展字段(table_id 等),不应被拒绝"""
        c = Chunk.model_validate({
            "doc_id": "d1",
            "chunk_id": "d1#c000001",
            "content": "x",
            "meta": {**self._valid_meta(), "table_id": "t1", "row_range": [0, 10]},
        })
        self.assertEqual(c.meta.model_dump().get("table_id"), "t1")


class TestRetrievedChunk(unittest.TestCase):
    def test_valid(self):
        rc = RetrievedChunk.model_validate({
            "chunk_id": "d1#c1",
            "doc_id": "d1",
            "file_name": "f.md",
            "chunk_index": 0,
            "score": 0.78,
        })
        self.assertEqual(rc.score, 0.78)

    def test_score_out_of_range_rejected(self):
        for bad in (-0.1, 1.5):
            with self.assertRaises(ValidationError):
                RetrievedChunk.model_validate({
                    "chunk_id": "d1#c1",
                    "doc_id": "d1",
                    "score": bad,
                })


class TestCitation(unittest.TestCase):
    def test_minimal(self):
        cit = Citation.model_validate({
            "chunk_id": "d1#c1",
            "doc_id": "d1",
        })
        self.assertIsNone(cit.file_name)
        self.assertIsNone(cit.score)

    def test_with_locations(self):
        cit = Citation.model_validate({
            "chunk_id": "d1#c1",
            "doc_id": "d1",
            "file_name": "f.md",
            "start_char": 100,
            "end_char": 200,
            "score": 0.66,
        })
        self.assertEqual(cit.start_char, 100)


class TestToolStep(unittest.TestCase):
    def test_valid(self):
        s = ToolStep.model_validate({
            "step_id": "s1",
            "tool_name": "rag_search",
            "description": "先检索",
            "input_data": {"query": "abc", "top_k": 3},
        })
        self.assertEqual(s.tool_name, "rag_search")
        self.assertEqual(s.input_data["top_k"], 3)

    def test_default_description_and_input_data(self):
        s = ToolStep.model_validate({"step_id": "s1", "tool_name": "x"})
        self.assertEqual(s.description, "")
        self.assertEqual(s.input_data, {})


if __name__ == "__main__":
    unittest.main()
