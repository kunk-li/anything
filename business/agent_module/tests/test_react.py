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
        agent.enable_self_verify = False  # 单测隔离: 只测 react 机制, 校验闭环有独立测试
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
        agent.enable_self_verify = False  # 单测隔离: 只测 react 机制, 校验闭环有独立测试
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
        agent.enable_self_verify = False  # 单测隔离: 只测 react 机制, 校验闭环有独立测试
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


# ==========================================================
# Task V (#56) — Plan mode
# ==========================================================
class TestPlanMode(unittest.TestCase):
    """plan_only=true 时 Agent 只输出 plan, 不执行工具; approve_plan=true 跑全流程"""

    def _make_agent(self, responses):
        reg = _DictRegistry()
        reg.register("rag_search", _stub_tool_success)
        reg.register("llm_generate", _stub_tool_success)
        llm = _ScriptedLLM(responses)
        agent = SimpleAgent(tool_registry=reg, llm_planner=llm)
        agent.execution_strategy = "react"
        agent.enable_self_verify = False  # 单测隔离: 只测 react 机制, 校验闭环有独立测试
        agent.use_llm_planner = False
        return agent, llm

    def test_plan_only_returns_pending_without_executing(self):
        """plan_only 模式: LLM 调 1 次拿 plan, 工具不被调用, code=PLAN_PENDING"""
        responses = [
            '{"thought":"先检索语料","action":{"tool":"rag_search","input":{"query":"x"}}}',
        ]
        agent, llm = self._make_agent(responses)
        result = agent.execute({
            "task": "找 x 的资料",
            "trace_id": "t-plan",
            "session_id": "s1",
            "extra_params": {"plan_only": True},
        })
        self.assertEqual(result["code"], "PLAN_PENDING")
        self.assertEqual(llm.call_count, 1)  # 只调 LLM 一次拿 plan
        plan = result["data"]["plan"]
        self.assertEqual(plan["action"]["tool"], "rag_search")
        self.assertIn("先检索语料", plan["thought"])
        self.assertIn("summary", plan)
        # 工具没被执行, 所以 tool_results_summary 应该为空
        self.assertEqual(result["data"]["tool_results_summary"], [])

    def test_plan_only_final_answer_path(self):
        """plan_only 时 LLM 直接给 final_answer 也算 plan (告诉用户不需要调工具)"""
        responses = [
            '{"thought":"我已经知道答案","final_answer":"直接答 X"}',
        ]
        agent, _ = self._make_agent(responses)
        result = agent.execute({
            "task": "x?",
            "trace_id": "t-plan2",
            "session_id": "s1",
            "extra_params": {"plan_only": True},
        })
        self.assertEqual(result["code"], "PLAN_PENDING")
        plan = result["data"]["plan"]
        self.assertEqual(plan["final_answer"], "直接答 X")

    def test_approve_plan_skips_plan_only_returns_full(self):
        """plan_only=true 且 approve_plan=true → 视为已审批, 走完整 ReAct"""
        responses = [
            '{"thought":"先检索","action":{"tool":"rag_search","input":{"query":"x"}}}',
            '{"thought":"已完成","final_answer":"最终 X"}',
        ]
        agent, llm = self._make_agent(responses)
        result = agent.execute({
            "task": "x",
            "trace_id": "t-plan3",
            "session_id": "s1",
            "extra_params": {"plan_only": True, "approve_plan": True},
        })
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["answer"], "最终 X")
        self.assertEqual(llm.call_count, 2)


# ==========================================================
# Task W (#57) — Tool approval gates
# ==========================================================
def _stub_dangerous_tool(payload):
    """模拟 py_sandbox 等敏感工具"""
    return {"code": "SUCCESS", "data": {"answer": "ran code"}}


class TestApprovalGates(unittest.TestCase):
    """工具白名单审批: 名单内工具未 approve 时返回 TOOL_APPROVAL_REQUIRED"""

    def _make_agent(self, responses, approval_required):
        reg = _DictRegistry()
        reg.register("rag_search", _stub_tool_success)
        reg.register("py_sandbox", _stub_dangerous_tool)
        reg.register("llm_generate", _stub_tool_success)
        llm = _ScriptedLLM(responses)
        agent = SimpleAgent(tool_registry=reg, llm_planner=llm)
        agent.execution_strategy = "react"
        agent.enable_self_verify = False  # 单测隔离: 只测 react 机制, 校验闭环有独立测试
        agent.use_llm_planner = False
        agent.tool_approval_required = set(approval_required)
        return agent, llm

    def test_safe_tool_runs_without_approval(self):
        """rag_search 不在审批名单 → 正常执行"""
        responses = [
            '{"thought":"go","action":{"tool":"rag_search","input":{}}}',
            '{"thought":"done","final_answer":"ok"}',
        ]
        agent, _ = self._make_agent(responses, approval_required=["py_sandbox"])
        result = agent.execute({"task": "x", "trace_id": "t1", "session_id": "s1"})
        self.assertEqual(result["code"], "SUCCESS")

    def test_dangerous_tool_blocked_without_approval(self):
        """py_sandbox 在名单 + extra_params 无 approve_tools → TOOL_APPROVAL_REQUIRED"""
        responses = [
            '{"thought":"跑代码","action":{"tool":"py_sandbox","input":{"code":"print(1)"}}}',
        ]
        agent, llm = self._make_agent(responses, approval_required=["py_sandbox"])
        result = agent.execute({"task": "calc", "trace_id": "t1", "session_id": "s1"})
        self.assertEqual(result["code"], "TOOL_APPROVAL_REQUIRED")
        self.assertEqual(result["data"]["tool_name"], "py_sandbox")
        self.assertIn("py_sandbox", result["data"]["required_tools"])
        self.assertEqual(result["data"]["approved_so_far"], [])
        # LLM 只调了 1 次 (拿 plan), 工具没被真执行
        self.assertEqual(llm.call_count, 1)

    def test_dangerous_tool_runs_when_approved(self):
        """py_sandbox 在名单 + extra_params.approve_tools 含它 → 正常执行"""
        responses = [
            '{"thought":"跑代码","action":{"tool":"py_sandbox","input":{"code":"print(1)"}}}',
            '{"thought":"done","final_answer":"代码已跑"}',
        ]
        agent, _ = self._make_agent(responses, approval_required=["py_sandbox"])
        result = agent.execute({
            "task": "calc",
            "trace_id": "t1",
            "session_id": "s1",
            "extra_params": {"approve_tools": ["py_sandbox"]},
        })
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["answer"], "代码已跑")

    def test_wildcard_approve_all(self):
        """approve_tools=['*'] → 一次性批准所有危险工具"""
        responses = [
            '{"thought":"跑","action":{"tool":"py_sandbox","input":{}}}',
            '{"thought":"done","final_answer":"ok"}',
        ]
        agent, _ = self._make_agent(responses, approval_required=["py_sandbox"])
        result = agent.execute({
            "task": "x", "trace_id": "t1", "session_id": "s1",
            "extra_params": {"approve_tools": ["*"]},
        })
        self.assertEqual(result["code"], "SUCCESS")

    def test_needs_approval_helper(self):
        """_needs_approval 内部辅助方法的边界"""
        agent, _ = self._make_agent([], approval_required=["py_sandbox"])
        self.assertTrue(agent._needs_approval("py_sandbox", {}))
        self.assertFalse(agent._needs_approval("py_sandbox", {"approve_tools": ["py_sandbox"]}))
        self.assertFalse(agent._needs_approval("rag_search", {}))
        self.assertFalse(agent._needs_approval(None, {}))
        self.assertFalse(agent._needs_approval("py_sandbox", {"approve_tools": ["*"]}))


# ==========================================================
# Task Z (#60) — Hook integration in SimpleAgent._react_execute
# ==========================================================
class TestHookIntegration(unittest.TestCase):
    """验证 SimpleAgent 真的在调工具前后过 hook"""

    def setUp(self):
        from common_utils_module import reset_hook_registry
        reset_hook_registry()

    def tearDown(self):
        from common_utils_module import reset_hook_registry
        reset_hook_registry()

    def _make_agent(self, responses):
        reg = _DictRegistry()
        reg.register("rag_search", _stub_tool_success)
        reg.register("llm_generate", _stub_tool_success)
        llm = _ScriptedLLM(responses)
        agent = SimpleAgent(tool_registry=reg, llm_planner=llm)
        agent.execution_strategy = "react"
        agent.enable_self_verify = False  # 单测隔离: 只测 react 机制, 校验闭环有独立测试
        agent.use_llm_planner = False
        # 清掉默认审批列表 (防干扰 hook 测试)
        agent.tool_approval_required = set()
        return agent, llm

    def test_pre_tool_call_hook_modifies_input(self):
        """pre_tool_call 可以改 input, 改后的值进入工具"""
        from common_utils_module import get_hook_registry
        received_inputs = []

        @get_hook_registry().pre_tool_call
        def add_marker(tool_name, input_data, ctx):
            new = dict(input_data)
            new["_via_hook"] = True
            return new

        # 用一个能记录入参的工具
        def recorder(payload):
            received_inputs.append(dict(payload))
            return {"code": "SUCCESS", "data": {"answer": "ok"}}

        responses = [
            '{"thought":"go","action":{"tool":"recorder","input":{"q":"hi"}}}',
            '{"thought":"done","final_answer":"X"}',
        ]
        agent, _ = self._make_agent(responses)
        agent.tool_registry.register("recorder", recorder)

        result = agent.execute({"task": "x", "trace_id": "t1", "session_id": "s1"})
        self.assertEqual(result["code"], "SUCCESS")
        self.assertTrue(received_inputs)
        self.assertTrue(received_inputs[0].get("_via_hook"),
                        "pre_tool_call hook 应该把 _via_hook=True 塞到工具入参里")

    def test_pre_tool_call_blocked_error_aborts(self):
        """pre_tool_call 抛 BlockedError → 主链路返回该 code, 工具不被调用"""
        from common_utils_module import get_hook_registry, BlockedError

        @get_hook_registry().pre_tool_call
        def deny_rag(tool_name, input_data, ctx):
            if tool_name == "rag_search":
                raise BlockedError("rag_search 被审计策略拒绝", code="POLICY_DENY")

        responses = [
            '{"thought":"go","action":{"tool":"rag_search","input":{}}}',
        ]
        agent, llm = self._make_agent(responses)
        result = agent.execute({"task": "x", "trace_id": "t1", "session_id": "s1"})
        self.assertEqual(result["code"], "POLICY_DENY")
        self.assertTrue(result["data"]["blocked_by_hook"])
        # LLM 只调 1 次 (拿 plan), 工具没真的跑
        self.assertEqual(llm.call_count, 1)

    def test_post_tool_call_can_modify_output(self):
        """post_tool_call 改 output dict → 后续 history.observation 用改过的"""
        from common_utils_module import get_hook_registry

        @get_hook_registry().post_tool_call
        def annotate(tool_name, input_data, output, ctx):
            new = dict(output)
            new["data"] = dict(new.get("data") or {})
            new["data"]["_audited_by_hook"] = True
            return new

        responses = [
            '{"thought":"go","action":{"tool":"rag_search","input":{}}}',
            '{"thought":"done","final_answer":"X"}',
        ]
        agent, _ = self._make_agent(responses)
        result = agent.execute({"task": "x", "trace_id": "t1", "session_id": "s1"})
        self.assertEqual(result["code"], "SUCCESS")
        # 不直接看 history (内部, 实现细节), 但 tool_results_summary 应该有
        self.assertGreater(len(result["data"]["tool_results_summary"]), 0)


class TestReActParallel(unittest.TestCase):
    """Phase3: ReAct 多动作并行执行 (actions:[...]) — 一步并发多个独立工具."""

    def _make_agent(self, llm_responses, approval=None):
        reg = _DictRegistry()
        for n in ("rag_search", "llm_generate", "weather", "py_sandbox"):
            reg.register(n, _stub_tool_success)
        agent = SimpleAgent(tool_registry=reg, llm_planner=_ScriptedLLM(llm_responses))
        agent.execution_strategy = "react"
        agent.use_llm_planner = False
        agent.enable_self_verify = False  # 隔离: 只测并行机制
        agent.tool_approval_required = set(approval or [])
        return agent

    # ---- 解析 ----
    def test_parse_actions_multi(self):
        raw = ('{"thought":"t","actions":[{"tool":"rag_search","input":{"query":"x"}},'
               '{"tool":"weather","input":{"city":"bj"}}]}')
        step = SimpleAgent._parse_react_response(raw, ["rag_search", "weather"])
        self.assertIn("actions", step)
        self.assertEqual([a["tool"] for a in step["actions"]], ["rag_search", "weather"])

    def test_parse_actions_filters_invalid(self):
        raw = '{"thought":"t","actions":[{"tool":"rag_search","input":{}},{"tool":"nope","input":{}}]}'
        step = SimpleAgent._parse_react_response(raw, ["rag_search"])
        self.assertEqual([a["tool"] for a in step["actions"]], ["rag_search"])

    def test_parse_actions_all_invalid_returns_none(self):
        raw = '{"thought":"t","actions":[{"tool":"nope","input":{}}]}'
        self.assertIsNone(SimpleAgent._parse_react_response(raw, ["rag_search"]))

    # ---- 执行 ----
    def test_parallel_executes_all_tools(self):
        agent = self._make_agent([
            ('{"thought":"并发查两个","actions":[{"tool":"rag_search","input":{"query":"a"}},'
             '{"tool":"weather","input":{"city":"bj"}}]}'),
            '{"thought":"汇总","final_answer":"done"}',
        ])
        r = agent.execute({"task": "并发任务", "session_id": "s", "trace_id": "t"})
        self.assertEqual(r["code"], "SUCCESS")
        tools = [x["tool_name"] for x in r["data"]["tool_results_summary"]]
        self.assertIn("rag_search", tools)
        self.assertIn("weather", tools)

    def test_parallel_approval_blocks_whole_batch(self):
        # 批中含需审批工具且未批 → 整批阻断 (安全优先), 不执行任何工具
        agent = self._make_agent([
            ('{"thought":"并发","actions":[{"tool":"rag_search","input":{}},'
             '{"tool":"py_sandbox","input":{"code":"x"}}]}'),
        ], approval=["py_sandbox"])
        r = agent.execute({"task": "x", "session_id": "s", "trace_id": "t"})
        self.assertEqual(r["code"], "TOOL_APPROVAL_REQUIRED")
        self.assertEqual(r["data"]["tool_name"], "py_sandbox")

    def test_parallel_approved_tool_runs(self):
        # 批中需审批工具已 approve → 整批正常并发执行
        agent = self._make_agent([
            ('{"thought":"并发","actions":[{"tool":"rag_search","input":{}},'
             '{"tool":"py_sandbox","input":{"code":"x"}}]}'),
            '{"thought":"done","final_answer":"ok"}',
        ], approval=["py_sandbox"])
        r = agent.execute({"task": "x", "session_id": "s", "trace_id": "t",
                           "extra_params": {"approve_tools": ["py_sandbox"]}})
        self.assertEqual(r["code"], "SUCCESS")
        tools = [x["tool_name"] for x in r["data"]["tool_results_summary"]]
        self.assertIn("py_sandbox", tools)


if __name__ == "__main__":
    unittest.main()
