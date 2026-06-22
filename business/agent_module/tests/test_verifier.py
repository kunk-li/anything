# -*- coding: utf-8 -*-
"""VER-2: verifier.py 核心单测 (mock runner/llm_call, 不接入主流程)。

验证: 4 个 Execution 场景 pass/fail + 异常处理 + TaskVerifier completed/缺口/
fail-open + ToolSuccess + run_verifiers 路由。
"""
import time
import unittest

from agent_module.core.impl import SimpleAgent
from agent_module.core.components.verifier import (
    VerifySpec,
    ToolSuccessVerifier, ExecutionVerifier, TaskVerifier, ComplianceVerifier,
    run_verifiers,
)


class _Reg:
    def __init__(self):
        self._t = {}

    def register(self, n, f):
        self._t[n] = f

    def unregister(self, n):
        return self._t.pop(n, None) is not None

    def get(self, n):
        return self._t.get(n)

    def list_tools(self):
        return list(self._t.keys())


class TestVerifySpec(unittest.TestCase):
    def test_from_obj_str_dict_invalid(self):
        self.assertEqual(VerifySpec.from_obj("pytest").type, "pytest")
        s = VerifySpec.from_obj({"type": "lint", "target": "a.py"})
        self.assertEqual((s.type, s.target), ("lint", "a.py"))
        self.assertIsNone(VerifySpec.from_obj({"no_type": 1}))
        self.assertIsNone(VerifySpec.from_obj(123))


class TestToolSuccessVerifier(unittest.TestCase):
    def test_success(self):
        r = ToolSuccessVerifier().verify(goal="g", result={"success": True},
                                         spec=VerifySpec(type="tool_success"))
        self.assertTrue(r.passed)

    def test_fail_carries_error(self):
        r = ToolSuccessVerifier().verify(goal="g", result={"success": False, "error": "boom"},
                                         spec=VerifySpec(type="tool_success"))
        self.assertFalse(r.passed)
        self.assertIn("boom", r.feedback)


class TestExecutionVerifier(unittest.TestCase):
    def _v(self, out):
        return ExecutionVerifier(runner=lambda spec: out)

    def test_pass_exit0_all_four_scenes(self):
        for t in ("pytest", "sql", "shell", "lint"):
            r = self._v({"exit_code": 0}).verify(goal="g", result=None, spec=VerifySpec(type=t))
            self.assertTrue(r.passed, t)

    def test_fail_carries_stderr_and_type(self):
        r = self._v({"exit_code": 1, "stderr": "AssertionError: x"}).verify(
            goal="g", result=None, spec=VerifySpec(type="pytest"))
        self.assertFalse(r.passed)
        self.assertIn("AssertionError", r.feedback)
        self.assertIn("pytest", r.feedback)

    def test_runner_exception_is_failed_not_raised(self):
        def boom(spec):
            raise RuntimeError("env down")
        r = ExecutionVerifier(runner=boom).verify(goal="g", result=None, spec=VerifySpec(type="shell"))
        self.assertFalse(r.passed)
        self.assertIn("env down", r.feedback)

    def test_unsupported_type_passes_unfixable(self):
        r = self._v({"exit_code": 1}).verify(goal="g", result=None, spec=VerifySpec(type="weird"))
        self.assertTrue(r.passed)       # 不支持 → 放行
        self.assertFalse(r.fixable)


class TestTaskVerifier(unittest.TestCase):
    def test_completed(self):
        v = TaskVerifier(llm_call=lambda p: '{"completed": true, "reason": "ok", "missing": ""}')
        r = v.verify(goal="做完 X", result={"answer": "已做完 X"}, spec=VerifySpec(type="task"))
        self.assertTrue(r.passed)

    def test_not_completed_carries_gap(self):
        v = TaskVerifier(llm_call=lambda p: '{"completed": false, "reason": "缺步骤2", "missing": "步骤2"}')
        r = v.verify(goal="g", result={"answer": "半成品"}, spec=VerifySpec(type="task"))
        self.assertFalse(r.passed)
        self.assertIn("步骤2", r.feedback)

    def test_llm_exception_fail_open(self):
        def boom(p):
            raise RuntimeError("llm down")
        r = TaskVerifier(llm_call=boom).verify(goal="g", result={"answer": "x"}, spec=VerifySpec(type="task"))
        self.assertTrue(r.passed)       # fail-open: 验证器挂了不判死任务
        self.assertFalse(r.fixable)

    def test_unparseable_fail_open(self):
        r = TaskVerifier(llm_call=lambda p: "没有json就觉得行").verify(
            goal="g", result={"answer": "x"}, spec=VerifySpec(type="task"))
        self.assertTrue(r.passed)

    def test_extracts_nested_answer(self):
        seen = {}
        def cap(p):
            seen["p"] = p
            return '{"completed": true}'
        TaskVerifier(llm_call=cap).verify(
            goal="g", result={"data": {"answer": "嵌套答案xyz"}}, spec=VerifySpec(type="task"))
        self.assertIn("嵌套答案xyz", seen["p"])


class TestRunVerifiers(unittest.TestCase):
    def test_routing_with_execution_alias(self):
        reg = {
            "tool_success": ToolSuccessVerifier(),
            "execution": ExecutionVerifier(runner=lambda s: {"exit_code": 0}),
            "task": TaskVerifier(llm_call=lambda p: '{"completed": true}'),
        }
        specs = [
            VerifySpec(type="pytest"),        # → execution 别名路由
            VerifySpec(type="tool_success"),
            VerifySpec(type="task"),
            VerifySpec(type="nope"),          # → 无验证器, 跳过放行
        ]
        results = run_verifiers(goal="g", result={"success": True, "answer": "a"},
                                specs=specs, registry=reg)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r.passed for r in results))

    def test_one_failing_among_many(self):
        reg = {"execution": ExecutionVerifier(runner=lambda s: {"exit_code": 1, "stderr": "fail"})}
        results = run_verifiers(goal="g", result=None,
                                specs=[VerifySpec(type="lint")], registry=reg)
        self.assertFalse(results[0].passed)


class TestSelfVerifyLoop(unittest.TestCase):
    """端到端: execute → 验证失败 → 自纠正递归 → 通过 (mock 执行核心+runner+llm)。"""

    def _agent(self):
        a = SimpleAgent(tool_registry=_Reg(), llm_planner=None)
        a.use_llm_planner = False
        a.enable_self_verify = True
        a.verify_mode = "auto"
        a.max_correction = 2
        # mock 执行核心: 固定 1 步 plan + 工具直接成功 (聚焦验证-纠正闭环本身)
        a.parse_task = lambda **kw: {
            "steps": [{"step_id": "s1", "tool_name": "noop", "input_data": {}}],
            "plan_source": "test",
        }
        a._call_tool_with_retry = lambda **kw: {
            "tool_name": "noop", "success": True, "output": {"data": {"answer": "done"}},
        }
        # task 终态验证恒通过, 把焦点放在 execution 验证的纠正上
        a._resolve_llm_planner = lambda trace_id=None: (lambda p: '{"completed": true}')
        return a

    def test_fail_then_correct_then_pass(self):
        a = self._agent()
        calls = {"n": 0}

        def runner(spec):
            calls["n"] += 1
            return {"exit_code": 1, "stderr": "lint error"} if calls["n"] == 1 else {"exit_code": 0}

        a._build_verify_runner = lambda: runner
        resp = a.execute({
            "task": "写并通过 lint", "trace_id": "t", "session_id": "s",
            "extra_params": {"verify": [{"type": "lint"}]},
        })
        self.assertTrue(resp["details"]["verify_passed"])   # 纠正后最终通过
        self.assertGreaterEqual(calls["n"], 2)              # 第1次失败 + 纠正后第2次

    def test_off_zero_impact(self):
        a = self._agent()
        a.enable_self_verify = False                        # 关
        resp = a.execute({
            "task": "x", "trace_id": "t", "session_id": "s",
            "extra_params": {"verify": [{"type": "lint"}]},
        })
        self.assertEqual(resp["code"], "SUCCESS")
        self.assertNotIn("verification", resp.get("details") or {})  # off → 完全不验证

    def test_budget_exhausted_marks_gap(self):
        a = self._agent()
        a.max_correction = 1
        a._build_verify_runner = lambda: (lambda spec: {"exit_code": 1, "stderr": "always fail"})
        resp = a.execute({
            "task": "永远失败的 lint", "trace_id": "t", "session_id": "s",
            "extra_params": {"verify": [{"type": "lint"}]},
        })
        self.assertFalse(resp["details"]["verify_passed"])
        self.assertIn("verify_gaps", resp["details"])


class TestSelfVerifyBudget(unittest.TestCase):
    """墙钟超时护栏: 自纠正递归承接外层 deadline, 不重开完整 timeout 窗口。"""

    def _agent(self):
        a = SimpleAgent(tool_registry=_Reg(), llm_planner=None)
        a.enable_self_verify = True
        a.verify_mode = "auto"
        a.max_correction = 5
        a.timeout = 30
        # 恒失败但可修 → 进入递归纠正分支
        a._build_verify_runner = lambda: (lambda spec: {"exit_code": 1, "stderr": "lint error"})
        a._resolve_llm_planner = lambda trace_id=None: (lambda p: '{"completed": true}')
        return a

    def _failing_response(self):
        return {"code": "SUCCESS", "data": {"answer": "x"}, "details": None}

    def test_recursion_threads_remaining_budget(self):
        a = self._agent()
        seen = {}

        def fake_execute(req):
            seen["timeout"] = req.get("timeout")
            return req            # 不真递归, 只捕获下传的 request
        a.execute = fake_execute

        # 外层已耗 5s, 配置 timeout=30 → 剩余 ~25s 应下传, 而非重置回 30
        resp = a._post_verify(
            request={"task": "t", "trace_id": "t", "session_id": "s",
                     "extra_params": {"verify": [{"type": "lint"}]}},
            response=self._failing_response(),
            original_task="t",
            start_time=time.time() - 5,
        )
        # 递归被触发 (返回的是被捕获的 new_request)
        self.assertTrue(callable(a.execute))
        self.assertIsNotNone(seen.get("timeout"))
        self.assertLessEqual(seen["timeout"], 26)   # 承接外层剩余, 明显 < 满额 30
        self.assertGreaterEqual(seen["timeout"], 1)
        self.assertEqual(resp.get("extra_params", {}).get("_verify_attempt"), 1)

    def test_no_recursion_when_outer_budget_exhausted(self):
        a = self._agent()
        called = {"n": 0}

        def fake_execute(req):
            called["n"] += 1
            return req
        a.execute = fake_execute

        # 外层墙钟已超过 timeout → within_time False → 不再递归, 直接标缺口返回
        resp = a._post_verify(
            request={"task": "t", "trace_id": "t", "session_id": "s",
                     "extra_params": {"verify": [{"type": "lint"}]}},
            response=self._failing_response(),
            original_task="t",
            start_time=time.time() - 31,
        )
        self.assertEqual(called["n"], 0)            # 超时后绝不重开新窗口
        self.assertFalse(resp["details"]["verify_passed"])
        self.assertIn("verify_gaps", resp["details"])

    def test_request_timeout_override_caps_recursion(self):
        a = self._agent()
        seen = {}

        def fake_execute(req):
            seen["timeout"] = req.get("timeout")
            return req
        a.execute = fake_execute

        # 调用方传了 timeout=10 (< self.timeout=30); deadline 应按 request.timeout 算,
        # 外层已耗 9s → 剩余 ~1s 下传, 不被 self.timeout 放大。
        a._post_verify(
            request={"task": "t", "trace_id": "t", "session_id": "s", "timeout": 10,
                     "extra_params": {"verify": [{"type": "lint"}]}},
            response=self._failing_response(),
            original_task="t",
            start_time=time.time() - 9,
        )
        self.assertIsNotNone(seen.get("timeout"))
        self.assertLessEqual(seen["timeout"], 2)
        self.assertGreaterEqual(seen["timeout"], 1)


class TestExecutionRunnerReal(unittest.TestCase):
    """VER-6: 真跑 _build_verify_runner (非 mock), 验证 4 场景框架真能工作。
    sql(sqlite 内存) / shell(cmd) 必可跑; pytest 项目本就装了。"""

    def _runner(self):
        return SimpleAgent(tool_registry=_Reg(), llm_planner=None)._build_verify_runner()

    def test_sql_real_pass_and_fail(self):
        r = self._runner()
        ok = r(VerifySpec(type="sql", target="CREATE TABLE t(a INT); INSERT INTO t VALUES (1);"))
        self.assertEqual(ok["exit_code"], 0)
        bad = r(VerifySpec(type="sql", target="SELECT * FROM no_such_table_xyz;"))
        self.assertNotEqual(bad["exit_code"], 0)
        self.assertTrue(bad["stderr"])

    def test_shell_real_pass_and_fail(self):
        r = self._runner()
        ok = r(VerifySpec(type="shell", target="echo verify_ok_marker"))
        self.assertEqual(ok["exit_code"], 0)
        self.assertIn("verify_ok_marker", ok["stdout"])
        bad = r(VerifySpec(type="shell", target="exit 3"))
        self.assertEqual(bad["exit_code"], 3)

    def test_pytest_real_failure_path(self):
        # 跑不存在的路径, pytest 应返回非 0 (验证 runner 真能捕获失败且不挂)
        out = self._runner()(VerifySpec(type="pytest", target="__no_such_test_dir_xyz__",
                                        args={"timeout": 30}))
        self.assertNotEqual(out["exit_code"], 0)


class TestComplianceVerifier(unittest.TestCase):
    """VER-7: 规范合规检查。"""

    def test_no_rules_passes(self):
        v = ComplianceVerifier(llm_call=lambda p: "", rules_provider=lambda: "")
        r = v.verify(goal="g", result={"answer": "x"}, spec=VerifySpec(type="compliance"))
        self.assertTrue(r.passed)   # 无规范 → 放行

    def test_compliant(self):
        v = ComplianceVerifier(llm_call=lambda p: '{"compliant": true}',
                               rules_provider=lambda: "禁用 eval")
        r = v.verify(goal="g", result={"answer": "用了 ast.literal_eval"}, spec=VerifySpec(type="compliance"))
        self.assertTrue(r.passed)

    def test_violation_carries_detail(self):
        v = ComplianceVerifier(llm_call=lambda p: '{"compliant": false, "violations": "直接用了 eval"}',
                               rules_provider=lambda: "禁用 eval")
        r = v.verify(goal="g", result={"answer": "eval(x)"}, spec=VerifySpec(type="compliance"))
        self.assertFalse(r.passed)
        self.assertIn("eval", r.feedback)

    def test_llm_exception_fail_open(self):
        def boom(p):
            raise RuntimeError("down")
        v = ComplianceVerifier(llm_call=boom, rules_provider=lambda: "some rule")
        r = v.verify(goal="g", result={"answer": "x"}, spec=VerifySpec(type="compliance"))
        self.assertTrue(r.passed)   # fail-open

    def test_rules_provider_exception_passes(self):
        def boom():
            raise RuntimeError("read fail")
        v = ComplianceVerifier(llm_call=lambda p: '{"compliant": false}', rules_provider=boom)
        r = v.verify(goal="g", result={"answer": "x"}, spec=VerifySpec(type="compliance"))
        self.assertTrue(r.passed)   # 取规范失败 → 放行


if __name__ == "__main__":
    unittest.main()
