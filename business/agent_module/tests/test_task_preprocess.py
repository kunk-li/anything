# -*- coding: utf-8 -*-
"""execute 前处理流水线 (执行计划④): 步骤顺序 / 早退短路 / 可组合 / 真 agent 无副作用。"""
import time
import unittest

from agent_module.core.impl import SimpleAgent
from agent_module.core.components.task_preprocess import TaskPreprocessMixin, TaskPreContext


def _ctx(task="t", **kw):
    base = dict(task=task, original_task=task, tenant_id="default", session_id="s",
                trace_id="x", extra_params={}, execution_mode="agent", start_time=0.0)
    base.update(kw)
    return TaskPreContext(**base)


class _Reg:
    def __init__(self): self._t = {}
    def register(self, n, f): self._t[n] = f
    def unregister(self, n): return self._t.pop(n, None) is not None
    def get(self, n): return self._t.get(n)
    def list_tools(self): return list(self._t.keys())


class _StubAgent(TaskPreprocessMixin):
    """stub 各步, 记录调用顺序。"""
    def __init__(self):
        self.calls = []
    def _pre_step_refine(self, ctx): self.calls.append("refine")
    def _pre_step_inject_memory(self, ctx): self.calls.append("memory")
    def _pre_step_inject_profile(self, ctx): self.calls.append("profile")
    def _pre_step_history(self, ctx): self.calls.append("history")
    def _pre_step_correction(self, ctx): self.calls.append("correction")


class TestPipelineMechanics(unittest.TestCase):
    def test_runs_all_steps_in_order(self):
        a = _StubAgent()
        early = a._preprocess_task(_ctx())
        self.assertIsNone(early)
        self.assertEqual(a.calls, ["refine", "memory", "profile", "history", "correction"])

    def test_early_response_short_circuits(self):
        class _A(_StubAgent):
            def _pre_step_inject_memory(self, ctx):
                self.calls.append("memory")
                ctx.early_response = {"code": "SUCCESS", "message": "clarification_needed"}
        a = _A()
        early = a._preprocess_task(_ctx())
        self.assertEqual(early, {"code": "SUCCESS", "message": "clarification_needed"})
        self.assertEqual(a.calls, ["refine", "memory"])   # 之后步骤不再跑

    def test_steps_composable_reorder_subset(self):
        a = _StubAgent()
        a.task_preprocess_steps = ("_pre_step_history", "_pre_step_refine")  # 重排 + 子集
        a._preprocess_task(_ctx())
        self.assertEqual(a.calls, ["history", "refine"])


class TestRealAgentNoop(unittest.TestCase):
    def test_noop_when_no_memory(self):
        # 无 long_term_memory → memory_enabled False → refine/memory/profile 跳过;
        # 无历史/无纠正反馈 → task 原样, 不早退
        agent = SimpleAgent(tool_registry=_Reg(), llm_planner=None)
        ctx = _ctx(task="你好", original_task="你好", session_id="s1", trace_id="t1",
                   start_time=time.time())
        early = agent._preprocess_task(ctx)
        self.assertIsNone(early)
        self.assertEqual(ctx.task, "你好")
        self.assertEqual(ctx.memory_hits, [])
        self.assertIsNone(ctx.refine_meta)


if __name__ == "__main__":
    unittest.main()
