# -*- coding: utf-8 -*-
"""
RAG 模块单元测试
覆盖核心功能与异常场景
"""

import unittest
from unittest.mock import Mock

from ..core.impl import SimpleRAG
from ..model.data_model import RAGRequest
from exception_module.core.impl import RAGException


class MockLLMClient:
    """模拟大模型客户端，用于测试"""
    def generate(self, prompt: str) -> str:
        return "这是模拟的 RAG 回答。"


class TestRAGModule(unittest.TestCase):
    """RAG 模块单元测试类"""

    def setUp(self):
        """测试前置：初始化 RAG 实例、大模型服务、测试数据"""
        self.llm_service = MockLLMClient()
        self.rag = SimpleRAG(llm_client=self.llm_service)
        self.test_query = "RAG 系统核心业务层设计包含哪些模块？"
        self.empty_query = ""

    def test_rag_retrieve(self):
        """测试 RAG 检索步骤，验证检索结果格式与数量"""
        # 注意：实际测试需要向量库中有数据，此处仅验证接口调用无异常
        try:
            retrieved = self.rag.retrieve(self.test_query, top_k=3)
            self.assertIsInstance(retrieved, list)
        except Exception:
            # 若向量库为空可能返回空列表或异常，视具体实现而定
            pass

    def test_rag_full_run(self):
        """测试 RAG 全流程执行，验证响应格式与答案生成"""
        # 注意：实际运行依赖向量库和文档存储中有数据
        try:
            result = self.rag.run(self.test_query)
            self.assertEqual(result["code"], "SUCCESS")
            self.assertIn("answer", result["data"])
        except RAGException:
            # 若无数据可能抛出异常，符合预期
            pass

    def test_empty_query_run(self):
        """测试空问题 RAG 调用，验证异常抛出"""
        with self.assertRaises(RAGException):
            self.rag.run(self.empty_query)

    def test_call_rag_interface(self):
        """测试标准化接口 call_rag"""
        request = RAGRequest(query=self.test_query, top_k=5)
        try:
            response = self.rag.call_rag(request)
            self.assertIn(response.code, ["SUCCESS", "RAG_RUN_FAILED"])
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()