# -*- coding: utf-8 -*-
"""
协同调度模块单元测试
覆盖核心功能与异常场景
"""

import unittest
from orchestrator_module.core.impl import SimpleOrchestrator
from orchestrator_module.model.data_model import OrchestratorRequest
from exception_module.core.impl import OrchestratorException


class MockRAG:
    """模拟 RAG 模块，用于测试"""
    def run(self, query, top_k):
        return {
            "code": "SUCCESS",
            "message": "ok",
            "data": {"answer": "mock rag answer"}
        }


class MockAgent:
    """模拟 Agent 模块，用于测试"""
    def execute(self, task, session_id):
        return {
            "code": "SUCCESS",
            "message": "ok",
            "data": {"result": "mock agent result"}
        }


class TestOrchestratorModule(unittest.TestCase):
    """协同调度模块单元测试类"""

    def setUp(self):
        """测试前置：初始化调度器实例、Mock 模块"""
        self.rag_mock = MockRAG()
        self.agent_mock = MockAgent()
        self.orchestrator = SimpleOrchestrator(
            rag_runner=self.rag_mock,
            agent_runner=self.agent_mock
        )

    def test_rag_route(self):
        """测试 RAG 模式路由"""
        request = {"type": "rag", "query": "test", "top_k": 5}
        result = self.orchestrator.route(request)
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["route_type"], "rag")

    def test_agent_route(self):
        """测试 Agent 模式路由"""
        request = {"type": "agent", "task": "test"}
        result = self.orchestrator.route(request)
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["route_type"], "agent")

    def test_hybrid_route(self):
        """测试 Hybrid 模式路由"""
        request = {"type": "hybrid", "task": "test"}
        result = self.orchestrator.route(request)
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["route_type"], "hybrid")

    def test_invalid_type(self):
        """测试非法 type 参数"""
        request = {"type": "invalid", "query": "test"}
        with self.assertRaises(OrchestratorException):
            self.orchestrator.route(request)

    def test_module_not_found(self):
        """测试模块未注册场景"""
        empty_orch = SimpleOrchestrator()
        request = {"type": "rag", "query": "test"}
        with self.assertRaises(OrchestratorException):
            empty_orch.route(request)

    def test_call_orchestrator_interface(self):
        """测试标准化接口 call_orchestrator"""
        request = OrchestratorRequest(type="rag", query="test")
        response = self.orchestrator.call_orchestrator(request)
        self.assertIn(response.code, ["SUCCESS", "ORCHESTRATOR_RUN_FAILED"])

    def test_module_register(self):
        """测试模块注册功能"""
        new_orch = SimpleOrchestrator()
        result = new_orch.register_module("rag", self.rag_mock)
        self.assertTrue(result)
        self.assertIn("rag", new_orch.modules)


if __name__ == "__main__":
    unittest.main()