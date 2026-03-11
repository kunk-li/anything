# -*- coding: utf-8 -*-
"""
Agent 模块单元测试
覆盖核心功能与异常场景
"""

import unittest
from agent_module.core.impl import SimpleAgent
from agent_module.model.data_model import AgentRequest
from exception_module.core.impl import AgentException


class MockTool:
    """模拟工具，用于测试"""

    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.call_count = 0

    def __call__(self, inp: dict):
        self.call_count += 1
        if self.should_fail:
            raise Exception("工具执行失败")
        return {
            "code": "SUCCESS",
            "message": "ok",
            "data": {"result": "mock_result"}
        }


class TestAgentModule(unittest.TestCase):
    """Agent 模块单元测试类"""

    def setUp(self):
        """测试前置：初始化 Agent 实例、测试数据"""
        self.tools = {
            "mock_tool": MockTool(),
            "fail_tool": MockTool(should_fail=True)
        }
        self.agent = SimpleAgent(tools=self.tools)
        self.test_task = "测试任务：请根据知识库整理要点"
        self.empty_task = ""

    def test_task_parse(self):
        """测试任务解析功能"""
        plan = self.agent.parse_task(self.test_task)
        self.assertIn("plan", plan)
        self.assertIsInstance(plan["plan"], list)

    def test_agent_execute(self):
        """测试 Agent 全流程执行"""
        result = self.agent.execute(
            self.test_task,
            session_id="test_session"
        )
        self.assertEqual(result["code"], "SUCCESS")
        self.assertIn("results", result["data"])

    def test_empty_task_execute(self):
        """测试空任务执行，验证异常抛出"""
        with self.assertRaises(AgentException):
            self.agent.execute(self.empty_task)

    def test_tool_retry(self):
        """测试工具重试机制"""
        fail_tool = MockTool(should_fail=True)
        self.agent.tool_registry.register("always_fail", fail_tool)

        with self.assertRaises(AgentException):
            self.agent.execute("调用 always_fail 工具")

        # 验证重试次数
        self.assertEqual(fail_tool.call_count, self.agent.max_retries)

    def test_call_agent_interface(self):
        """测试标准化接口 call_agent"""
        request = AgentRequest(
            task=self.test_task,
            session_id="test_session"
        )
        response = self.agent.call_agent(request)
        self.assertIn(response.code, ["SUCCESS", "AGENT_EXECUTE_FAILED"])

    def test_tool_register_unregister(self):
        """测试工具注册与注销"""

        def new_tool(inp):
            return {"result": "new"}

        # 注册
        self.assertTrue(self.agent.register_tool(
            "new_tool",
            new_tool,
            "新工具",
            {}
        ))
        self.assertIsNotNone(self.agent.tool_registry.get("new_tool"))

        # 注销
        self.assertTrue(self.agent.unregister_tool("new_tool"))
        self.assertIsNone(self.agent.tool_registry.get("new_tool"))

    def test_search_task_parse(self):
        """测试检索类任务解析"""
        plan = self.agent.parse_task("请检索知识库中关于 RAG 的资料")
        self.assertEqual(plan["plan"][0]["tool"], "rag_search")

    def test_calculate_task_parse(self):
        """测试计算类任务解析"""
        plan = self.agent.parse_task("计算 123 + 456 的结果")
        self.assertEqual(plan["plan"][0]["tool"], "calculator")


if __name__ == "__main__":
    unittest.main()