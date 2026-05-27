# -*- coding: utf-8 -*-
"""
请求响应处理模块单元测试

覆盖:
    - validate_request 三元组 (is_valid, message, code) 契约
    - handle 端到端: 成功路径 / 校验失败 / 下游异常
    - format_response 统一信封字段
    - _standardize_request 默认值与 trace_id 注入
"""

import unittest

from request_response_module.core.impl import RequestHandler


class MockOrchestrator:
    """模拟协同调度模块"""

    def route(self, request):
        rtype = request.get("type")
        if rtype == "rag":
            return {
                "code": "SUCCESS",
                "message": "ok",
                "data": {"answer": "mock rag answer"},
            }
        if rtype == "agent":
            return {
                "code": "SUCCESS",
                "message": "ok",
                "data": {"result": "mock agent result"},
            }
        # hybrid 故意抛异常,测试 handle 的异常包装
        raise RuntimeError("模拟下游异常")


class TestRequestHandlerValidate(unittest.TestCase):
    """validate_request: 现在统一返回 3 元组 (is_valid, message, code)"""

    def setUp(self):
        self.handler = RequestHandler(orchestrator=MockOrchestrator())

    def test_valid_rag_request(self):
        is_valid, msg, code = self.handler.validate_request(
            {"type": "rag", "query": "test", "top_k": 5}
        )
        self.assertTrue(is_valid)
        self.assertEqual(code, "SUCCESS")

    def test_valid_agent_request(self):
        is_valid, msg, code = self.handler.validate_request(
            {"type": "agent", "task": "test"}
        )
        self.assertTrue(is_valid)
        self.assertEqual(code, "SUCCESS")

    def test_missing_query_for_rag(self):
        is_valid, msg, code = self.handler.validate_request({"type": "rag"})
        self.assertFalse(is_valid)
        self.assertEqual(code, "PARAM_MISSING")

    def test_missing_task_for_agent(self):
        is_valid, msg, code = self.handler.validate_request({"type": "agent"})
        self.assertFalse(is_valid)
        self.assertEqual(code, "PARAM_MISSING")

    def test_invalid_type(self):
        is_valid, msg, code = self.handler.validate_request(
            {"type": "invalid", "query": "test"}
        )
        self.assertFalse(is_valid)
        self.assertEqual(code, "BAD_REQUEST")

    def test_invalid_top_k_out_of_range(self):
        is_valid, msg, code = self.handler.validate_request(
            {"type": "rag", "query": "test", "top_k": 100}
        )
        self.assertFalse(is_valid)
        self.assertEqual(code, "PARAM_INVALID")

    def test_invalid_top_k_wrong_type(self):
        is_valid, msg, code = self.handler.validate_request(
            {"type": "rag", "query": "test", "top_k": "abc"}
        )
        self.assertFalse(is_valid)
        self.assertEqual(code, "PARAM_INVALID")


class TestRequestHandlerHandle(unittest.TestCase):
    """handle: 端到端请求处理"""

    def setUp(self):
        self.handler = RequestHandler(orchestrator=MockOrchestrator())

    def test_handle_success_rag(self):
        response = self.handler.handle({"type": "rag", "query": "test"})
        self.assertEqual(response["code"], "SUCCESS")
        self.assertIn("trace_id", response)
        self.assertIn("data", response)
        self.assertEqual(response["data"]["answer"], "mock rag answer")

    def test_handle_success_agent(self):
        response = self.handler.handle({"type": "agent", "task": "test"})
        self.assertEqual(response["code"], "SUCCESS")
        self.assertIn("trace_id", response)

    def test_handle_validation_failure(self):
        """请求缺 query 时直接被 validate 拦下,返回 PARAM_MISSING 不进 orchestrator。"""
        response = self.handler.handle({"type": "rag"})
        self.assertEqual(response["code"], "PARAM_MISSING")
        self.assertIn("trace_id", response)

    def test_handle_downstream_exception(self):
        """orchestrator 抛异常时 handle 必须包装为统一信封返回(不能 raise 出去)。"""
        response = self.handler.handle({"type": "hybrid", "task": "test"})
        self.assertNotEqual(response["code"], "SUCCESS")
        self.assertIn("trace_id", response)

    def test_handle_external_trace_id_preserved(self):
        """外部传入的 trace_id 应原样保留(不重新生成)。"""
        response = self.handler.handle(
            {"type": "rag", "query": "test"},
            trace_id="ext_trace_123",
        )
        self.assertEqual(response["trace_id"], "ext_trace_123")


class TestRequestHandlerFormat(unittest.TestCase):
    """format_response: 统一响应信封字段"""

    def setUp(self):
        self.handler = RequestHandler(orchestrator=MockOrchestrator())

    def test_format_success_envelope(self):
        response = self.handler.format_response(
            code="SUCCESS",
            message="ok",
            data={"x": 1},
            trace_id="t1",
        )
        self.assertEqual(response["code"], "SUCCESS")
        self.assertEqual(response["retryable"], False)
        self.assertIsNone(response["details"])
        self.assertEqual(response["trace_id"], "t1")

    def test_format_error_envelope(self):
        response = self.handler.format_response(
            code="PARAM_MISSING",
            message="缺少必填参数",
            data=None,
            trace_id="t1",
        )
        self.assertEqual(response["code"], "PARAM_MISSING")
        # 失败响应应该带 details
        self.assertIsNotNone(response["details"])


class TestRequestHandlerStandardize(unittest.TestCase):
    """_standardize_request: 默认值与 trace_id 注入"""

    def setUp(self):
        self.handler = RequestHandler(orchestrator=MockOrchestrator())

    def test_default_type_and_top_k(self):
        std = self.handler._standardize_request({"query": "test"}, trace_id="t1")
        self.assertEqual(std["type"], "rag")  # 默认 type
        self.assertEqual(std["top_k"], 5)  # 默认 top_k

    def test_trace_id_injected(self):
        std = self.handler._standardize_request({"type": "rag", "query": "x"}, trace_id="t1")
        self.assertEqual(std.get("trace_id"), "t1")


if __name__ == "__main__":
    unittest.main()
