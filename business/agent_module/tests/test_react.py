# -*- coding: utf-8 -*-
"""
SimpleAgent ReAct 模式单元测试

覆盖:
    - 多轮 observe-reflect-next 循环
    - LLM 输出 final_answer -> 提前终止
    - LLM 调了未注册工具 -> fallback 到 single_shot
    - LLM JSON 解析失败 -> fallback
    - LLM 抛异常 -> fallback
    - 达到 max_react_iterations -> 用最后 observation 作为答案
    - 无 LLM 通道 -> fallback
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


class _ScriptedLLM:
    """按预定义脚本依次返回 LLM 输出 — 模拟多轮对话"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0

    def __call__(self, prompt: str) -> str:
        self.call_count += 1
        if self.responses:
            return self.responses.pop(0)
        return ""  # 用完脚本后返回空


def _stub_tool_success(payload):
    return {"code": "SUCCESS", "message": "ok", "data": {"text": "stub_result"}}


class TestReActExecute(unittest.TestCase):

    def _make_agent(self, llm_responses, strategy="react"):
        reg = _DictRegistry()
        reg.register("llm_generate", _stub_tool_success)
        reg.register("rag_search", _stub_tool_success)
        llm = _ScriptedLLM(llm_responses)
        agent = SimpleAgent(
            tool_registry=reg,
            llm_planner=llm,
        )
        agent.execution_strategy = strategy
        agent.use_llm_planner = False  # parse_task 用规则式,避免跟 ReAct 路径混
        return agent, llm

    # ---------------- 正常路径 ----------------

    def test_react_two_iter_then_final(self):
        """LLM 第一轮调工具, 第二轮给出 final_answer -> 共 2 轮"""
        responses = [
            '{"thought":"先检索","action":{"tool":"rag_search","input":{"query":"abc"}}}',
            '{"thought":"已有足够信息","final_answer":"最终答案 X"}',
        ]
        agent, llm = self._make_agent(responses)
        result = agent.execute({
            "task": "回答 abc 相关问题",
            "trace_id": "t1",
            "session_id": "s1",
        })
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["answer"], "最终答案 X")
        self.assertEqual(result["data"]["execution_strategy"], "react")
        self.assertEqual(result["data"]["iterations_used"], 2)
        self.assertEqual(llm.call_count, 2)

    def test_react_first_iter_final(self):
        """LLM 第一轮即给 final_answer -> 不调工具"""
        responses = [
            '{"thought":"直接回答","final_answer":"答 Y"}',
        ]
        agent, llm = self._make_agent(responses)
        result = agent.execute({"task": "...", "trace_id": "t1", "session_id": "s1"})
        self.assertEqual(result["data"]["answer"], "答 Y")
        self.assertEqual(result["data"]["iterations_used"], 1)
        self.assertEqual(len(result["data"]["tool_results_summary"]), 0)

    def test_react_history_recorded(self):
        responses = [
            '{"thought":"think 1","action":{"tool":"rag_search","input":{}}}',
            '{"thought":"think 2","final_answer":"done"}',
        ]
        agent, _ = self._make_agent(responses)
        result = agent.execute({"task": "x", "trace_id": "t1", "session_id": "s1"})
        history = result["data"]["react_history"]
        self.assertEqual(len(history), 2)
        self.assertIn("think 1", history[0]["thought"])
        self.assertEqual(history[1]["final_answer"], "done")

    # ---------------- max iterations ----------------

    def test_react_max_iterations_uses_last_observation(self):
        """超过 max_react_iterations 且无 final_answer -> 用最后 observation 作答"""
        responses = [
            '{"thought":"t1","action":{"tool":"llm_generate","input":{"prompt":"a"}}}',
        ] * 10  # 每轮都调工具,从不 final
        agent, _ = self._make_agent(responses)
        agent.max_react_iterations = 3
        result = agent.execute({"task": "x", "trace_id": "t1", "session_id": "s1"})
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["iterations_used"], 3)
        # 答案应来自最后一次工具调用的结果
        self.assertIn("stub", result["data"]["answer"].lower())

    # ---------------- Fallback ----------------

    def test_react_invalid_json_falls_back_to_single_shot(self):
        """LLM 返回不是合法 JSON -> _react_execute 返回 None -> 走 single_shot"""
        agent, _ = self._make_agent(["不是 JSON"])
        result = agent.execute({"task": "x", "trace_id": "t1", "session_id": "s1"})
        self.assertEqual(result["code"], "SUCCESS")
        # 走 single_shot 后 execution_strategy 字段不存在,或不是 "react"
        self.assertNotEqual(
            result["data"].get("execution_strategy"), "react"
        )

    def test_react_unknown_tool_falls_back(self):
        """LLM 调了 registry 里没有的工具 -> _parse_react_response 拒绝 -> fallback"""
        agent, _ = self._make_agent([
            '{"thought":"t","action":{"tool":"unknown_tool","input":{}}}'
        ])
        result = agent.execute({"task": "x", "trace_id": "t1", "session_id": "s1"})
        self.assertEqual(result["code"], "SUCCESS")
        self.assertNotEqual(result["data"].get("execution_strategy"), "react")

    def test_react_llm_exception_falls_back(self):
        def raise_llm(p):
            raise RuntimeError("LLM down")
        reg = _DictRegistry()
        reg.register("llm_generate", _stub_tool_success)
        agent = SimpleAgent(tool_registry=reg, llm_planner=raise_llm)
        agent.execution_strategy = "react"
        agent.use_llm_planner = False

        result = agent.execute({"task": "x", "trace_id": "t1", "session_id": "s1"})
        self.assertEqual(result["code"], "SUCCESS")
        self.assertNotEqual(result["data"].get("execution_strategy"), "react")

    def test_react_no_llm_channel_falls_back(self):
        """没有 llm_planner & registry 无 llm_generate -> 直接 fallback"""
        reg = _DictRegistry()
        reg.register("rag_search", _stub_tool_success)  # 故意不注册 llm_generate
        agent = SimpleAgent(tool_registry=reg, llm_planner=None)
        agent.execution_strategy = "react"
        agent.use_llm_planner = False
        result = agent.execute({"task": "x", "trace_id": "t1", "session_id": "s1"})
        self.assertEqual(result["code"], "SUCCESS")
        self.assertNotEqual(result["data"].get("execution_strategy"), "react")

    def test_react_hybrid_mode_skips_react(self):
        """hybrid 模式按文档定义为固定 rag + llm 两步,即使配置 react 也跳过"""
        agent, llm = self._make_agent([
            '{"thought":"t","final_answer":"x"}'
        ])
        result = agent.execute({
            "task": "...",
            "trace_id": "t1",
            "session_id": "s1",
            "extra_params": {"execution_mode": "hybrid"},
        })
        # hybrid 走 single_shot 规则式,不进 ReAct
        self.assertEqual(llm.call_count, 0)

    def test_per_request_strategy_override(self):
        """单次请求可通过 extra_params.execution_strategy 覆盖全局策略"""
        responses = [
            '{"thought":"t","final_answer":"single shot 答案"}',
        ]
        reg = _DictRegistry()
        reg.register("llm_generate", _stub_tool_success)
        agent = SimpleAgent(tool_registry=reg, llm_planner=_ScriptedLLM(responses))
        agent.execution_strategy = "single_shot"  # 全局 single_shot
        agent.use_llm_planner = False

        # 但本次请求显式要求 react
        result = agent.execute({
            "task": "x",
            "trace_id": "t1",
            "session_id": "s1",
            "extra_params": {"execution_strategy": "react"},
        })
        self.assertEqual(result["data"]["answer"], "single shot 答案")
        self.assertEqual(result["data"].get("execution_strategy"), "react")


if __name__ == "__main__":
    unittest.main()
