# -*- coding: utf-8 -*-
"""AUDIT-2a: Agent wall-clock 超时 enforce 测试。

验证 doc/错误码表承诺的 AGENT_TIMEOUT 真的会在执行超时时触发,
而不是像历史那样 timeout 只写进 log payload 从不生效。
覆盖 ReAct 多轮路径 + single_shot 顺序执行路径。
"""
import time
import unittest

from agent_module.core.impl import SimpleAgent


class _Reg:
    def __init__(self):
        self._t = {"llm_generate": lambda p: {"code": "SUCCESS", "data": {"text": "x"}}}

    def register(self, n, f):
        self._t[n] = f

    def unregister(self, n):
        return self._t.pop(n, None) is not None

    def get(self, n):
        return self._t.get(n)

    def list_tools(self):
        return list(self._t.keys())


class TestAgentTimeoutEnforce(unittest.TestCase):

    def test_react_returns_agent_timeout_when_deadline_already_passed(self):
        """ReAct 循环每轮入口检查 wall-clock: start_time 远在过去 → 第一轮就 AGENT_TIMEOUT。"""
        agent = SimpleAgent(tool_registry=_Reg(), llm_planner=None)
        # mock 让 _react_execute 能进主循环 (无需真实 LLM)
        agent._resolve_llm_planner = lambda trace_id=None: (
            lambda prompt: '{"thought":"t","action":{"tool":"llm_generate","input":{}}}'
        )
        agent._available_tool_names = lambda: ["llm_generate"]

        past = time.time() - 9999  # 9999s 前"开始", 必然超过任何正常 timeout
        result = agent._react_execute(
            task="做点啥", session_id="s1", trace_id="t1",
            extra_params={}, start_time=past, timeout=5,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["code"], "AGENT_TIMEOUT")
        self.assertEqual(result["data"]["iterations_used"], 0)  # 第一轮就超时, 0 轮完成
        self.assertTrue(result["retryable"])

    def test_react_completes_normally_when_within_timeout(self):
        """对照组: start_time=now + 充足 timeout → 不超时, 正常走 final_answer 返回 SUCCESS。

        证明超时检查不是无脑触发, 正常路径不受影响。
        """
        agent = SimpleAgent(tool_registry=_Reg(), llm_planner=None)
        agent._resolve_llm_planner = lambda trace_id=None: (
            lambda prompt: '{"thought":"done","final_answer":"答案就是42"}'
        )
        agent._available_tool_names = lambda: ["llm_generate"]
        result = agent._react_execute(
            task="x", session_id="s1", trace_id="t1",
            extra_params={}, start_time=time.time(), timeout=60,
        )
        self.assertEqual(result["code"], "SUCCESS")
        self.assertIn("42", result["data"]["answer"])

    def test_stream_timeout_finalizes_with_partial_results(self):
        """流式 ReAct 超时: 不再 done(AGENT_TIMEOUT) 丢弃已收集结果 (前端收 0 chunk
        渲染空白气泡), 而是 loop_break → 收尾流程基于已有工具结果产出答案 chunks。"""
        agent = SimpleAgent(tool_registry=_Reg(), llm_planner=None)

        def _slow_llm(prompt):
            time.sleep(1.2)  # 单次 LLM 调用就吃满 timeout=1
            return '{"thought":"查一下","action":{"tool":"llm_generate","input":{}}}'

        agent._resolve_llm_planner = lambda trace_id=None: _slow_llm
        agent._available_tool_names = lambda: ["llm_generate"]

        events = list(agent.run_stream({
            "task": "做点啥", "trace_id": "t1", "session_id": "s1", "timeout": 1,
            "extra_params": {"execution_strategy": "react"},
        }))
        types = [e.get("type") for e in events]
        # 第 1 轮完成后第 2 轮入口检测到超时 → loop_break (不是直接 done)
        self.assertIn("loop_break", types)
        # 收尾流程照常产出 meta + 答案 chunks + done
        self.assertIn("meta", types)
        self.assertIn("chunk", types)
        self.assertEqual(types[-1], "done")
        done = events[-1]
        self.assertEqual(done.get("code"), "SUCCESS")
        # chunk 总内容非空 (铁底兜底保证) — 旧 bug 是 0 chunk
        total = "".join(e.get("text", "") for e in events if e.get("type") == "chunk")
        self.assertTrue(total.strip())

    def test_single_shot_timeout_marks_agent_timeout(self):
        """single_shot 步骤循环超时 → 中止后续步骤, response code=AGENT_TIMEOUT。"""
        agent = SimpleAgent(tool_registry=_Reg(), llm_planner=None)
        agent.use_llm_planner = False
        # mock 出 2 步计划
        agent.parse_task = lambda **kw: {
            "steps": [
                {"step_id": "s1", "tool_name": "slow", "input_data": {}},
                {"step_id": "s2", "tool_name": "slow", "input_data": {}},
            ],
            "plan_source": "test",
        }
        calls = {"n": 0}

        def _slow(step, session_id, trace_id, max_retries):
            calls["n"] += 1
            if calls["n"] == 1:
                time.sleep(1.1)  # 第一步耗时, 使第二步循环入口检查时已超 timeout=1
            return {"tool_name": "slow", "success": True,
                    "output": {"data": {"answer": "部分结果"}}}

        agent._call_tool_with_retry = _slow
        result = agent.execute({
            "task": "x", "trace_id": "t1", "session_id": "s1", "timeout": 1,
        })
        self.assertEqual(result["code"], "AGENT_TIMEOUT")
        self.assertEqual(calls["n"], 1)  # 只执行了第一步, 第二步被超时中止


if __name__ == "__main__":
    unittest.main()
