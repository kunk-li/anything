# -*- coding: utf-8 -*-
"""
请求响应处理模块单元测试
覆盖核心功能与异常场景
"""

import unittest
from request_response_module.core.impl import RequestHandler
from request_response_module.model.data_model import UnifiedRequest
from exception_module.core.impl import ExceptionHandler


class MockOrchestrator:
    """模拟协同调度模块，用于测试"""
    def route(self, request):
        if request.get("type") == "rag":
            return {
                "code": "SUCCESS",
                "message": "ok",
                "data": {"answer": "mock rag answer"}
            }
        elif request.get("type") == "agent":
            return {
                "code": "SUCCESS",
                "message": "ok",
                "data": {"result": "mock agent result"}
            }
        else:
            raise Exception("调度异常")


class TestRequestHandler(unittest.TestCase):
    """请求响应处理模块单元测试类"""

    def setUp(self):
        """测试前置：初始化处理器实例、Mock 调度模块"""
        self.orchestrator = MockOrchestrator()
        self.handler = RequestHandler(orchestrator=self.orchestrator)

    def test_validate_rag_request(self):
        """测试 RAG 请求校验"""
        request = {"type": "rag", "query": "test", "top_k": 5}
        is_valid, error_msg = self.handler.validate_request(request)
        self.assertTrue(is_valid)

    def test_validate_agent_request(self):
        """测试 Agent 请求校验"""
        request = {"type": "agent", "task": "test"}
        is_valid, error_msg = self.handler.validate_request(request)
        self.assertTrue(is_valid)

    def test_validate_missing_query(self):
        """测试缺少 query 参数校验"""
        request = {"type": "rag"}
        is_valid, error_msg = self.handler.validate_request(request)
        self.assertFalse(is_valid)
        self.assertIn("query", error_msg)

    def test_validate_invalid_type(self):
        """测试非法 type 参数校验"""
        request = {"type": "invalid", "query": "test"}
        is_valid, error_msg = self.handler.validate_request(request)
        self.assertFalse(is_valid)
        self.assertIn("不支持的请求类型", error_msg)

    def test_validate_invalid_top_k(self):
        """测试非法 top_k 参数校验"""
        request = {"type": "rag", "query": "test", "top_k": 100}
        is_valid, error_msg = self.handler.validate_request(request)
        self.assertFalse(is_valid)
        self.assertIn("top_k", error_msg)

    def test_handle_success_rag(self):
        """测试成功 RAG 请求处理"""
        request = {"type": "rag", "query": "test"}
        response = self.handler.handle(request)
        self.assertEqual(response["code"], "SUCCESS")
        self.assertIn("trace_id", response)
        self.assertIn("data", response)

    def test_handle_success_agent(self):
        """测试成功 Agent 请求处理"""
        request = {"type": "agent", "task": "test"}
        response = self.handler.handle(request)
        self.assertEqual(response["code"], "SUCCESS")
        self.assertIn("trace_id", response)

    def test_handle_exception(self):
        """测试异常处理"""
        request = {"type": "hybrid", "task": "test"}  # Mock 会抛出异常
        response = self.handler.handle(request)
        self.assertNotEqual(response["code"], "SUCCESS")
        self.assertIn("trace_id", response)

    def test_format_response_success(self):
        """测试成功响应格式化"""
        response = self.handler.format_response(
            code="SUCCESS",
            message="ok",
            data={"test": "data"},
            trace_id="test_trace"
        )
        self.assertEqual(response["code"], "SUCCESS")
        self.assertEqual(response["retryable"], False)
        self.assertIsNone(response["details"])

    def test_format_response_error(self):
        """测试失败响应格式化"""
        response = self.handler.format_response(
            code="PARAM_MISSING",
            message="缺少必填参数",
            data=None,
            trace_id="test_trace"
        )
        self.assertEqual(response["code"], "PARAM_MISSING")
        self.assertIsNotNone(response["details"])

    def test_standardize_request(self):
        """测试请求标准化"""
        request = {"query": "test"}
        standardized = self.handler._standardize_request(request)
        self.assertEqual(standardized["type"], "rag")  # 默认 type
        self.assertEqual(standardized["top_k"], 5)  # 默认 top_k
        self.assertIn("session_id", standardized)
        self.assertIn("timestamp", standardized)


if __name__ == "__main__":
    unittest.main()