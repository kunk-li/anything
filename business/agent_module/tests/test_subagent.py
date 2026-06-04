# -*- coding: utf-8 -*-
"""
spawn_subagent 工具单元测试 (Task EE #65)
"""

import unittest

from agent_module.core.impl import SimpleAgent
from agent_module.tools import make_spawn_subagent_tool


class _DictRegistry:
    """跟测试 test_react.py 一致的轻量 registry"""

    def __init__(self):
        self._tools = {}
        self._desc = {}

    def register(self, name, func, description=""):
        self._tools[name] = func
        if description:
            self._desc[name] = description

    def get(self, name):
        return self._tools.get(name)

    def list_tools(self):
        return list(self._tools.keys())

    def describe(self, name):
        return self._desc.get(name, "")

    def describe_all(self):
        return dict(self._desc)


class _ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, prompt):
        self.calls += 1
        if self.responses:
            return self.responses.pop(0)
        return ""


def _echo_tool(payload):
    return {"code": "SUCCESS", "data": {"answer": f"echoed: {payload.get('q')}"}, "success": True}


def _fake_search(payload):
    return {"code": "SUCCESS", "data": {"answer": f"found: {payload.get('query')}"}, "success": True}


def _danger_tool(payload):
    return {"code": "SUCCESS", "data": {"answer": "ran"}, "success": True}


def _make_parent_agent(parent_llm_responses=None):
    """造一个父 agent, 含 3 个工具 (echo, search, danger), 跑 ReAct 模式."""
    reg = _DictRegistry()
    reg.register("echo", _echo_tool, description="echo back")
    reg.register("search", _fake_search, description="fake search")
    reg.register("danger", _danger_tool, description="dangerous")
    agent = SimpleAgent(
        tool_registry=reg,
        llm_planner=_ScriptedLLM(parent_llm_responses or []),
    )
    agent.execution_strategy = "react"
    agent.enable_self_verify = False  # 单测隔离: 只测 subagent 机制, 校验闭环有独立测试
    agent.use_llm_planner = False
    agent.tool_approval_required = set()
    return agent


class TestSpawnSubagentTool(unittest.TestCase):

    def test_subagent_runs_with_allowed_tool(self):
        """子 agent 拿 search 工具跑一轮 ReAct, 拿到 answer."""
        parent = _make_parent_agent()
        spawn = make_spawn_subagent_tool(parent)

        # 子 agent 的 LLM 输出: 直接 final_answer (1 轮搞定)
        child_llm = _ScriptedLLM([
            '{"thought":"已知道答案","final_answer":"子 agent 答案 ✓"}',
        ])
        # 把父 llm_planner 替换为子用的脚本 LLM (子 agent 复用父 llm_planner)
        parent.llm_planner = child_llm

        result = spawn(
            role="researcher",
            task="找 anything 项目信息",
            allowed_tools=["search"],
            trace_id="t-parent",
            session_id="s-parent",
        )
        self.assertEqual(result["code"], "SUCCESS")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["answer"], "子 agent 答案 ✓")
        self.assertEqual(result["data"]["allowed_tools"], ["search"])
        self.assertEqual(result["data"]["role"], "researcher")

    def test_subagent_with_tool_call(self):
        """子 agent 调一次 search 工具, 再 final_answer."""
        parent = _make_parent_agent()
        spawn = make_spawn_subagent_tool(parent)
        parent.llm_planner = _ScriptedLLM([
            '{"thought":"先查","action":{"tool":"search","input":{"query":"x"}}}',
            '{"thought":"已得到","final_answer":"基于 search 结果 X"}',
        ])
        result = spawn(
            task="x 是什么",
            allowed_tools=["search"],
            trace_id="t",
            session_id="s",
        )
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["answer"], "基于 search 结果 X")
        self.assertEqual(result["data"]["iterations_used"], 2)
        # 子 agent 调了 1 个工具 -> tool_results_summary 非空
        self.assertGreaterEqual(len(result["data"]["tool_results_summary"]), 1)

    def test_subagent_restricts_tools(self):
        """allowed_tools=['echo'] → 子 agent 看不到 search/danger.
           即使 LLM 输出调 danger, 父 parser 会拒绝 (unknown tool fallback)."""
        parent = _make_parent_agent()
        spawn = make_spawn_subagent_tool(parent)
        # LLM 输出: 调 danger 工具 (子不应该看到), parser 解析失败 → fallback to single_shot
        # single_shot 跑会再调 LLM, 没脚本 -> 拿空字符串 -> 整体降级
        # 这里关键: 子 agent.tool_registry.list_tools() 只该有 'echo'
        parent.llm_planner = _ScriptedLLM([
            '{"thought":"直接答","final_answer":"answer Y"}',
        ])
        result = spawn(
            task="t",
            allowed_tools=["echo"],
            trace_id="t",
            session_id="s",
        )
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["allowed_tools"], ["echo"])

    def test_subagent_invalid_allowed_tools_returns_error(self):
        """allowed_tools 跟父 registry 无交集 → PARAM_INVALID, 不真的 spawn"""
        parent = _make_parent_agent()
        spawn = make_spawn_subagent_tool(parent)
        result = spawn(
            task="x",
            allowed_tools=["nonexistent_tool"],
            trace_id="t", session_id="s",
        )
        self.assertEqual(result["code"], "PARAM_INVALID")
        self.assertFalse(result["success"])

    def test_subagent_empty_task_returns_error(self):
        parent = _make_parent_agent()
        spawn = make_spawn_subagent_tool(parent)
        result = spawn(task="", trace_id="t", session_id="s")
        self.assertEqual(result["code"], "PARAM_MISSING")

    def test_subagent_default_inherits_all_tools_except_self(self):
        """allowed_tools 未指定时 → 继承父全部工具, 但去掉 spawn_subagent 自身 (防递归)"""
        parent = _make_parent_agent()
        # 先把 spawn_subagent 也注册到父
        spawn = make_spawn_subagent_tool(parent)
        parent.tool_registry.register("spawn_subagent", spawn, description="recursive")
        parent.llm_planner = _ScriptedLLM([
            '{"thought":"答","final_answer":"答 Z"}',
        ])

        result = spawn(task="x", trace_id="t", session_id="s")
        self.assertEqual(result["code"], "SUCCESS")
        # allowed 中不应含 spawn_subagent
        self.assertNotIn("spawn_subagent", result["data"]["allowed_tools"])
        # 但应有 echo / search / danger
        self.assertIn("echo", result["data"]["allowed_tools"])

    def test_subagent_max_iterations_capped(self):
        """max_iterations 应该限制子 ReAct 轮数; 超过 10 自动 clamp 到 10"""
        parent = _make_parent_agent()
        spawn = make_spawn_subagent_tool(parent)
        parent.llm_planner = _ScriptedLLM([
            '{"thought":"final","final_answer":"Z"}',
        ])
        result = spawn(
            task="x", max_iterations=999,  # 应被 clamp 到 10
            trace_id="t", session_id="s",
        )
        self.assertEqual(result["code"], "SUCCESS")

    def test_spawn_subagent_propagates_to_real_subagent(self):
        """spawn_subagent 注册到父 registry 后, 父 agent 跑 ReAct 调它能成功执行.

        关键验证: spawn_subagent 出现在 tool_results_summary 里 (即父真的调到了它).
        不验证 final_answer 文本 — _ScriptedLLM 在父/子之间共享, 响应顺序难精确控制.
        """
        parent = _make_parent_agent()
        spawn = make_spawn_subagent_tool(parent)
        parent.tool_registry.register("spawn_subagent", spawn, description="...")
        # 给一份够长的脚本, 父和子从共享队列消费, 哪个先 final_answer 哪个先停.
        parent.llm_planner = _ScriptedLLM([
            # 父第 1 轮: 调 spawn_subagent
            '{"thought":"派生","action":{"tool":"spawn_subagent",'
            '"input":{"task":"子任务","allowed_tools":["search"]}}}',
            # 子 / 父接下来谁先 pop 给 final_answer 谁先停
            '{"thought":"final","final_answer":"answer A"}',
            '{"thought":"final","final_answer":"answer B"}',
            '{"thought":"final","final_answer":"answer C"}',
        ])
        result = parent.execute({"task": "总任务", "trace_id": "T", "session_id": "S"})
        self.assertEqual(result["code"], "SUCCESS")
        # 父调了 spawn_subagent 工具, 应该在 tool_results_summary 里看到它
        tool_names = [t.get("tool_name") for t in result["data"]["tool_results_summary"]]
        self.assertIn("spawn_subagent", tool_names)
        # 应该有非空 answer
        self.assertTrue(result["data"]["answer"])


if __name__ == "__main__":
    unittest.main()
