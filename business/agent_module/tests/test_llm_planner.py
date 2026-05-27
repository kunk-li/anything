# -*- coding: utf-8 -*-
"""
Agent LLM 规划路径单元测试

覆盖:
    - LLM 返回合法 JSON -> 走 LLM 规划 (plan_source=llm)
    - LLM 返回无效 JSON -> fallback 规则式
    - LLM 返回不存在的工具 -> fallback 规则式
    - 无 LLM 通道 (无 llm_planner & 无 tool_registry) -> 走规则式
    - hybrid 模式 -> 强制走规则式
"""

import unittest

from agent_module.core.impl import SimpleAgent


class _DictRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, name, func):
        self._tools[name] = func

    def get(self, name):
        return self._tools.get(name)

    def list_tools(self):
        return list(self._tools.keys())


def _noop_tool(payload):
    return {"code": "SUCCESS", "message": "ok", "data": {"text": "stub"}}


class TestLLMPlanner(unittest.TestCase):

    def _make_agent(self, llm_planner=None, with_llm_generate=True):
        """构造带 rag_search + llm_generate 工具的 agent。"""
        reg = _DictRegistry()
        reg.register("rag_search", _noop_tool)
        if with_llm_generate:
            reg.register("llm_generate", _noop_tool)
        return SimpleAgent(
            tool_registry=reg,
            llm_planner=llm_planner,
        )

    def test_llm_returns_valid_plan(self):
        """LLM 返回合法 JSON -> plan_source=llm,使用 LLM 决策。"""
        def fake_llm(prompt: str) -> str:
            return (
                '{"steps": ['
                '{"step_id":"s1","tool_name":"rag_search","description":"先检索",'
                '"input_data":{"query":"abc","top_k":3}},'
                '{"step_id":"s2","tool_name":"llm_generate","description":"再总结",'
                '"input_data":{"prompt":"abc"}}'
                ']}'
            )

        agent = self._make_agent(llm_planner=fake_llm)
        plan = agent.parse_task(task="abc", session_id="sess1", trace_id="t1")

        self.assertEqual(plan["plan_source"], "llm")
        self.assertEqual(len(plan["steps"]), 2)
        self.assertEqual(plan["steps"][0]["tool_name"], "rag_search")
        self.assertEqual(plan["steps"][1]["tool_name"], "llm_generate")
        # trace_id 应被补全到 input_data
        self.assertEqual(plan["steps"][0]["input_data"]["trace_id"], "t1")

    def test_llm_returns_invalid_json_falls_back(self):
        """LLM 返回不能解析为 JSON 的文本 -> fallback 规则式。"""
        def fake_llm(prompt: str) -> str:
            return "这不是 JSON,只是占位回答"

        agent = self._make_agent(llm_planner=fake_llm)
        plan = agent.parse_task(task="写个计划", session_id="sess1", trace_id="t1")

        self.assertEqual(plan["plan_source"], "rule_based")
        # agent 模式默认只 llm_generate 一步
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(plan["steps"][0]["tool_name"], "llm_generate")

    def test_llm_returns_unknown_tool_falls_back(self):
        """LLM 调了 registry 里不存在的工具 -> 整个 plan 拒绝,fallback。"""
        def fake_llm(prompt: str) -> str:
            return (
                '{"steps": ['
                '{"step_id":"s1","tool_name":"unknown_tool","input_data":{}}'
                ']}'
            )

        agent = self._make_agent(llm_planner=fake_llm)
        plan = agent.parse_task(task="...", session_id="sess1", trace_id="t1")

        self.assertEqual(plan["plan_source"], "rule_based")

    def test_no_llm_channel_uses_rule_based(self):
        """无 llm_planner 注入且 registry 无 llm_generate -> 直接走规则式。"""
        agent = self._make_agent(llm_planner=None, with_llm_generate=False)
        plan = agent.parse_task(task="...", session_id="sess1", trace_id="t1")

        self.assertEqual(plan["plan_source"], "rule_based")

    def test_hybrid_mode_forces_rule_based(self):
        """hybrid 模式按文档定义为固定 rag + llm 两步,跳过 LLM 规划。"""
        def fake_llm(prompt: str) -> str:
            # 即使 LLM 准备好了合法 JSON,hybrid 也不应该走 LLM 规划
            return '{"steps":[{"step_id":"s1","tool_name":"llm_generate","input_data":{}}]}'

        agent = self._make_agent(llm_planner=fake_llm)
        plan = agent.parse_task(
            task="基于知识库回答",
            session_id="sess1",
            trace_id="t1",
            extra_params={"execution_mode": "hybrid"},
        )

        self.assertEqual(plan["plan_source"], "rule_based")
        self.assertEqual(len(plan["steps"]), 2)
        self.assertEqual(plan["steps"][0]["tool_name"], "rag_search")
        self.assertEqual(plan["steps"][1]["tool_name"], "llm_generate")

    def test_llm_throws_exception_falls_back(self):
        """LLM 调用过程抛异常 -> fallback,不影响主流程。"""
        def fake_llm(prompt: str) -> str:
            raise RuntimeError("LLM 连接失败")

        agent = self._make_agent(llm_planner=fake_llm)
        plan = agent.parse_task(task="abc", session_id="sess1", trace_id="t1")

        self.assertEqual(plan["plan_source"], "rule_based")

    def test_llm_markdown_wrapped_json_parses(self):
        """LLM 用 markdown ``` 包裹 JSON 也应解析成功。"""
        def fake_llm(prompt: str) -> str:
            return (
                "```json\n"
                '{"steps": [{"step_id":"s1","tool_name":"llm_generate","input_data":{}}]}\n'
                "```"
            )

        agent = self._make_agent(llm_planner=fake_llm)
        plan = agent.parse_task(task="abc", session_id="sess1", trace_id="t1")

        self.assertEqual(plan["plan_source"], "llm")
        self.assertEqual(plan["steps"][0]["tool_name"], "llm_generate")

    def test_max_planner_steps_truncates(self):
        """LLM 输出过多步骤时被 max_planner_steps 截断。"""
        def fake_llm(prompt: str) -> str:
            steps_json = ",".join([
                '{"step_id":"s%d","tool_name":"llm_generate","input_data":{}}' % i
                for i in range(1, 10)
            ])
            return '{"steps":[' + steps_json + ']}'

        agent = self._make_agent(llm_planner=fake_llm)
        plan = agent.parse_task(task="abc", session_id="sess1", trace_id="t1")

        self.assertEqual(plan["plan_source"], "llm")
        # 默认 max_planner_steps=3
        self.assertLessEqual(len(plan["steps"]), 3)


if __name__ == "__main__":
    unittest.main()
