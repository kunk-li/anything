# -*- coding: utf-8 -*-
"""
RAG 模块单元测试

覆盖核心功能、签名契约、异常路径。

设计说明:
    本测试不真实初始化向量库/文档存储,因此 retrieve / run 调用主要验证
    "请求/响应结构契约",而不验证检索质量(后者由 smoke test 端到端验证)。
"""

import unittest

# 绝对 import,避免 unittest discover 时相对 import 失败
from rag_module.core.impl import SimpleRAG


class MockLLMClient:
    """模拟大模型客户端"""

    def generate(self, prompt, trace_id=None):
        return "这是模拟的 RAG 回答。"


class TestSimpleRAG(unittest.TestCase):
    """RAG 模块单元测试"""

    def setUp(self):
        # 仅注入 llm_client,其他依赖保持为 None
        # (retrieve 在 vector_db=None 时直接返回 [],不会报错)
        self.llm = MockLLMClient()
        self.rag = SimpleRAG(llm_client=self.llm)

    def test_retrieve_with_no_vector_db_returns_empty(self):
        """没有 vector_db 时 retrieve 应返回空列表而非崩溃。"""
        chunks = self.rag.retrieve({
            "query": "什么是 RAG?",
            "top_k": 3,
            "trace_id": "t1",
        })
        self.assertIsInstance(chunks, list)
        self.assertEqual(chunks, [])

    def test_run_returns_unified_envelope(self):
        """run 应返回统一响应信封 (code/message/data/trace_id/...)。"""
        result = self.rag.run({
            "query": "什么是 RAG?",
            "top_k": 3,
            "trace_id": "t1",
        })
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["trace_id"], "t1")
        self.assertIn("answer", result["data"])
        self.assertIn("citations", result["data"])
        self.assertIn("retrieved_chunks", result["data"])

    def test_run_empty_query_still_returns_envelope(self):
        """空 query 在 RAG 层不抛异常(校验由 RequestHandler 边界负责),
        但应能返回明确的响应结构。"""
        result = self.rag.run({
            "query": "",
            "top_k": 3,
            "trace_id": "t1",
        })
        # 不论成功失败,都必须返回统一信封字段
        self.assertIn("code", result)
        self.assertIn("trace_id", result)

    def test_call_rag_keyword_interface(self):
        """call_rag 关键字参数风格入口(BaseRAG 契约)。"""
        result = self.rag.call_rag(
            query="什么是 RAG?",
            top_k=3,
            trace_id="t1",
            session_id="s1",
        )
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["trace_id"], "t1")


if __name__ == "__main__":
    unittest.main()
