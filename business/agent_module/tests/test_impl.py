# -*- coding: utf-8 -*-
"""
Agent 模块单元测试(核心契约)

覆盖:
    - SimpleAgent 构造与基础属性
    - execute 返回统一响应信封
    - register_tool / unregister_tool 契约
    - call_agent 关键字参数入口

注意:
    LLM 规划相关测试见同目录 test_llm_planner.py。
"""

import unittest

from agent_module.core.impl import SimpleAgent


class _DictRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, name, func):
        self._tools[name] = func

    def unregister(self, name):
        return self._tools.pop(name, None) is not None

    def get(self, name):
        return self._tools.get(name)

    def list_tools(self):
        return list(self._tools.keys())


def _stub_llm_generate(payload):
    """模拟 llm_generate 工具的成功响应。"""
    return {
        "code": "SUCCESS",
        "message": "ok",
        "data": {"text": "stub answer for: " + str(payload.get("prompt", ""))[:32]},
    }


def _stub_rag_search(payload):
    return {
        "code": "SUCCESS",
        "message": "ok",
        "data": {
            "answer": "rag stub answer",
            "citations": [],
            "retrieved_chunks": [],
        },
    }


class TestSimpleAgent(unittest.TestCase):

    def setUp(self):
        # impl 接收的是 tool_registry,不是 tools(注意:这是历史测试的常见错误)
        self.registry = _DictRegistry()
        self.registry.register("llm_generate", _stub_llm_generate)
        self.registry.register("rag_search", _stub_rag_search)
        # 显式关闭 LLM 规划,让 parse_task 走规则式以便测试稳定
        self.agent = SimpleAgent(
            tool_registry=self.registry,
            llm_planner=None,
        )
        # 关掉 LLM 规划,避免 _stub_llm_generate 偶然返回 JSON-like 字符串触发 LLM 路径
        self.agent.use_llm_planner = False

    def test_execute_returns_unified_envelope(self):
        """execute 应返回统一响应信封,且 data 含 answer / steps。"""
        result = self.agent.execute({
            "task": "请写一段开发计划",
            "trace_id": "t1",
            "session_id": "s1",
        })
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["trace_id"], "t1")
        data = result["data"]
        self.assertIn("answer", data)
        self.assertIn("steps", data)
        self.assertEqual(data["session_id"], "s1")

    def test_register_tool_returns_true(self):
        """register_tool 应满足 BaseAgent.-> bool 契约。"""
        def my_tool(payload):
            return {"code": "SUCCESS", "data": {}}

        ok = self.agent.register_tool(
            name="my_tool",
            tool_func=my_tool,
            description="test",
            input_schema={},
        )
        self.assertTrue(ok)
        self.assertIsNotNone(self.registry.get("my_tool"))

    def test_unregister_tool(self):
        """unregister_tool 应该真的从 registry 移除工具。"""
        # 先注册再注销
        self.agent.register_tool("tmp_tool", lambda p: {}, "tmp", {})
        self.assertIsNotNone(self.registry.get("tmp_tool"))

        ok = self.agent.unregister_tool("tmp_tool")
        self.assertTrue(ok)
        self.assertIsNone(self.registry.get("tmp_tool"))

    def test_unregister_nonexistent_tool_returns_false(self):
        ok = self.agent.unregister_tool("never_registered")
        self.assertFalse(ok)

    def test_call_agent_keyword_interface(self):
        """call_agent 关键字参数入口(BaseAgent 契约)。"""
        result = self.agent.call_agent(
            task="请写一段开发计划",
            trace_id="t1",
            session_id="s1",
        )
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["trace_id"], "t1")

    def test_execute_hybrid_mode_uses_two_steps(self):
        """hybrid 模式(规则式)应固定为 rag_search + llm_generate 两步。"""
        result = self.agent.execute({
            "task": "基于知识库回答问题",
            "trace_id": "t1",
            "session_id": "s1",
            "extra_params": {"execution_mode": "hybrid"},
        })
        self.assertEqual(result["code"], "SUCCESS")
        steps = result["data"]["steps"]
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["tool_name"], "rag_search")
        self.assertEqual(steps[1]["tool_name"], "llm_generate")


if __name__ == "__main__":
    unittest.main()
