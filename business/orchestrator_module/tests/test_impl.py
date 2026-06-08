# -*- coding: utf-8 -*-
"""
协同调度模块单元测试
覆盖核心功能与异常场景。

注: 本文件此前因 `OrchestratorException` 未定义而 **collection error** 跑不了; 修复后暴露
出例子全是 **陈旧断言** (旧 orchestrator 契约)。已对齐当前实现:
  - 下游执行器统一用单个 request dict 调 (rag.run(req) / agent.execute(req));
  - route() 不再往 data 套 route_type ("不再额外套娃包装");
  - 非法 type / 执行器未注册 返回 **错误 envelope** (BAD_REQUEST / RAG_RUN_FAILED), 不再 raise。
"""

import unittest

from orchestrator_module.core.impl import SimpleOrchestrator
from orchestrator_module.utils.tool_functions import validate_request_params
from exception_module.core.impl import OrchestratorException


class MockRAG:
    """模拟 RAG 执行器: 与当前契约一致, run(request_dict) -> envelope。"""
    def run(self, request):
        return {"code": "SUCCESS", "message": "ok", "data": {"answer": "mock rag answer"}}


class MockAgent:
    """模拟 Agent 执行器: 与当前契约一致, execute(request_dict) -> envelope。"""
    def execute(self, request):
        return {"code": "SUCCESS", "message": "ok", "data": {"result": "mock agent result"}}


class TestOrchestratorModule(unittest.TestCase):
    """协同调度模块单元测试类"""

    def setUp(self):
        self.rag_mock = MockRAG()
        self.agent_mock = MockAgent()
        self.orchestrator = SimpleOrchestrator(
            rag_runner=self.rag_mock,
            agent_runner=self.agent_mock,
        )

    def test_rag_route(self):
        """RAG 模式路由 → 透传下游 envelope (不套 route_type)"""
        result = self.orchestrator.route({"type": "rag", "query": "test", "top_k": 5})
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["answer"], "mock rag answer")

    def test_agent_route(self):
        """Agent 模式路由"""
        result = self.orchestrator.route({"type": "agent", "task": "test"})
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["result"], "mock agent result")

    def test_hybrid_route(self):
        """Hybrid 模式 = Agent 主导 → 走 agent.execute"""
        result = self.orchestrator.route({"type": "hybrid", "task": "test"})
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["result"], "mock agent result")

    def test_invalid_type_returns_bad_request(self):
        """非法 type → 返回 BAD_REQUEST envelope (当前实现不再 raise)"""
        result = self.orchestrator.route({"type": "invalid", "query": "test"})
        self.assertEqual(result["code"], "BAD_REQUEST")
        self.assertEqual(result["details"]["actual"], "invalid")

    def test_module_not_registered_returns_error_envelope(self):
        """执行器未注册 → RAG_RUN_FAILED envelope (当前实现不再 raise)"""
        empty_orch = SimpleOrchestrator()
        result = empty_orch.route({"type": "rag", "query": "test"})
        self.assertEqual(result["code"], "RAG_RUN_FAILED")

    def test_call_orchestrator_interface(self):
        """兼容旧接口 call_orchestrator → 转调 route(), 返回 dict envelope"""
        response = self.orchestrator.call_orchestrator({"type": "rag", "query": "test"})
        self.assertEqual(response["code"], "SUCCESS")

    def test_register_module(self):
        """register_module 设置对应执行器 (当前实现返回 None, 不再有 .modules)"""
        new_orch = SimpleOrchestrator()
        new_orch.register_module("rag", self.rag_mock)
        self.assertIs(new_orch.rag_runner, self.rag_mock)
        with self.assertRaises(ValueError):       # 未知模块类型 → ValueError
            new_orch.register_module("nope", self.rag_mock)


class TestValidateRequestParams(unittest.TestCase):
    """tool_functions.validate_request_params: 顺带覆盖之前无测试的工具 + 验证 OrchestratorException。"""

    def test_valid_passes(self):
        self.assertTrue(validate_request_params({"type": "rag", "query": "x"}))
        self.assertTrue(validate_request_params({"type": "agent", "task": "x"}))

    def test_bad_type_raises(self):
        with self.assertRaises(OrchestratorException):
            validate_request_params({"type": "nope", "query": "x"})

    def test_rag_missing_query_raises(self):
        with self.assertRaises(OrchestratorException):
            validate_request_params({"type": "rag"})

    def test_agent_missing_task_raises(self):
        with self.assertRaises(OrchestratorException):
            validate_request_params({"type": "agent"})


if __name__ == "__main__":
    unittest.main()
