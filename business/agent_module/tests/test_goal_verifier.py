# -*- coding: utf-8 -*-
"""方向3 GoalVerifier: 子目标级验收 (验证时分解, opt-in, additive, 不改规划核心)。

验证: 全达成/部分未达带缺口+score / 显式子目标(spec.args & ctx)进 prompt /
LLM 异常 & 坏 JSON fail-open / 无子目标 fallback / 嵌套 answer 抽取;
+ collect_specs(verify_goals 开关) / make_registry(注册 goal) / run_verifiers 路由。
"""
import unittest

from agent_module.core.components.verifier import (
    VerifySpec, GoalVerifier, run_verifiers, collect_specs, make_registry,
)

_OK_JSON = '{"sub_goals":[{"goal":"x","met":true,"missing":""}],"all_met":true}'


class TestGoalVerifier(unittest.TestCase):
    def test_all_sub_goals_met_passes(self):
        v = GoalVerifier(llm_call=lambda p: (
            '{"sub_goals":[{"goal":"A","met":true,"missing":""},'
            '{"goal":"B","met":true,"missing":""}],"all_met":true}'))
        r = v.verify(goal="做 A 和 B", result={"answer": "A 和 B 都做了"}, spec=VerifySpec(type="goal"))
        self.assertTrue(r.passed)
        self.assertEqual(r.score, 1.0)

    def test_some_unmet_fails_with_gaps_and_score(self):
        v = GoalVerifier(llm_call=lambda p: (
            '{"sub_goals":[{"goal":"加认证","met":false,"missing":"没有 token 刷新"},'
            '{"goal":"写测试","met":true,"missing":""}],"all_met":false}'))
        r = v.verify(goal="加认证并写测试", result={"answer": "做了一半"}, spec=VerifySpec(type="goal"))
        self.assertFalse(r.passed)
        self.assertIn("token 刷新", r.feedback)
        self.assertIn("加认证", r.feedback)
        self.assertEqual(r.score, 0.5)        # 2 中达成 1
        self.assertTrue(r.fixable)            # 可纠正

    def test_explicit_goals_from_spec_args(self):
        seen = {}
        def cap(p):
            seen["p"] = p
            return _OK_JSON
        GoalVerifier(llm_call=cap).verify(
            goal="总目标", result={"answer": "a"},
            spec=VerifySpec(type="goal", args={"goals": ["子目标甲", "子目标乙"]}))
        self.assertIn("子目标甲", seen["p"])
        self.assertIn("子目标乙", seen["p"])
        self.assertIn("逐项", seen["p"])      # 走"显式验收"分支

    def test_explicit_goals_from_ctx(self):
        seen = {}
        def cap(p):
            seen["p"] = p
            return _OK_JSON
        GoalVerifier(llm_call=cap).verify(
            goal="g", result={"answer": "a"}, spec=VerifySpec(type="goal"),
            ctx={"sub_goals": ["来自ctx的子目标"]})
        self.assertIn("来自ctx的子目标", seen["p"])

    def test_auto_decompose_when_no_explicit(self):
        seen = {}
        def cap(p):
            seen["p"] = p
            return _OK_JSON
        GoalVerifier(llm_call=cap).verify(
            goal="盖个房子", result={"answer": "盖好了"}, spec=VerifySpec(type="goal"))
        self.assertIn("拆成", seen["p"])      # 走"现场拆解"分支

    def test_llm_exception_fail_open(self):
        def boom(p):
            raise RuntimeError("llm down")
        r = GoalVerifier(llm_call=boom).verify(goal="g", result={"answer": "x"}, spec=VerifySpec(type="goal"))
        self.assertTrue(r.passed)             # fail-open
        self.assertFalse(r.fixable)

    def test_unparseable_fail_open(self):
        r = GoalVerifier(llm_call=lambda p: "没有 json 就放行").verify(
            goal="g", result={"answer": "x"}, spec=VerifySpec(type="goal"))
        self.assertTrue(r.passed)

    def test_no_sub_goals_falls_back_to_all_met(self):
        r = GoalVerifier(llm_call=lambda p: '{"sub_goals":[],"all_met":true}').verify(
            goal="g", result={"answer": "x"}, spec=VerifySpec(type="goal"))
        self.assertTrue(r.passed)

    def test_extracts_nested_answer(self):
        seen = {}
        def cap(p):
            seen["p"] = p
            return _OK_JSON
        GoalVerifier(llm_call=cap).verify(
            goal="g", result={"data": {"answer": "嵌套答案abc"}}, spec=VerifySpec(type="goal"))
        self.assertIn("嵌套答案abc", seen["p"])


class TestCollectSpecsGoal(unittest.TestCase):
    def test_verify_goals_off_by_default(self):
        types = [s.type for s in collect_specs({"verify": []})]
        self.assertNotIn("goal", types)
        self.assertIn("task", types)          # task 仍默认在

    def test_verify_goals_on_appends_goal_spec_with_explicit(self):
        specs = collect_specs({"verify_goals": True, "goals": ["g1", "g2"]})
        goal_specs = [s for s in specs if s.type == "goal"]
        self.assertEqual(len(goal_specs), 1)
        self.assertEqual(goal_specs[0].args.get("goals"), ["g1", "g2"])

    def test_make_registry_has_goal(self):
        reg = make_registry(runner=lambda s: {"exit_code": 0}, llm_call=lambda p: "{}")
        self.assertIn("goal", reg)
        self.assertIsInstance(reg["goal"], GoalVerifier)


class TestGoalVerifierRouting(unittest.TestCase):
    def test_run_verifiers_routes_goal(self):
        reg = make_registry(
            runner=lambda s: {"exit_code": 0},
            llm_call=lambda p: '{"sub_goals":[{"goal":"a","met":false,"missing":"缺a"}],"all_met":false}')
        results = run_verifiers(goal="g", result={"answer": "x"},
                                specs=[VerifySpec(type="goal")], registry=reg)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].passed)
        self.assertEqual(results[0].verifier, "goal")


if __name__ == "__main__":
    unittest.main()
