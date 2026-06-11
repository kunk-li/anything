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
    def _pre_step_attachments(self, ctx): self.calls.append("attachments")


class TestPipelineMechanics(unittest.TestCase):
    def test_runs_all_steps_in_order(self):
        a = _StubAgent()
        early = a._preprocess_task(_ctx())
        self.assertIsNone(early)
        self.assertEqual(a.calls, ["refine", "memory", "profile", "history",
                                   "correction", "attachments"])

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


class TestAttachmentsStep(unittest.TestCase):
    """附件块注入 (方案B): 前端只传元信息, 工具选择交给 ReAct。"""

    def setUp(self):
        self.agent = SimpleAgent(tool_registry=_Reg(), llm_planner=None)

    def test_suffix_empty_when_no_attachments(self):
        self.assertEqual(self.agent._attachments_task_suffix({}), "")
        self.assertEqual(self.agent._attachments_task_suffix({"attachments": []}), "")

    def test_suffix_skips_garbage_entries(self):
        # 非 dict / 既无 path 又无 doc_id 的条目不渲染; 全是垃圾 → 返空串
        ep = {"attachments": ["x", 42, {"name": "孤儿.txt"}]}
        self.assertEqual(self.agent._attachments_task_suffix(ep), "")
        self.assertEqual(self.agent._attachments_task_suffix({"attachments": "oops"}), "")

    def test_suffix_renders_path_and_doc_id(self):
        ep = {"attachments": [
            {"name": "photo.png", "mime": "image/png", "path": "uploads/photo.png"},
            {"name": "报告.pdf", "mime": "application/pdf",
             "path": "uploads/报告.pdf", "doc_id": "abc-123"},
        ]}
        s = self.agent._attachments_task_suffix(ep)
        self.assertIn("[用户附件]", s)
        self.assertIn('1. photo.png (image/png) — 文件路径: "uploads/photo.png"', s)
        self.assertIn("doc_id: abc-123 (已入库)", s)
        self.assertIn("image_describe", s)   # 类型→工具映射提示在块尾

    def test_step_appends_to_task(self):
        ep = {"attachments": [{"name": "a.pdf", "path": "uploads/a.pdf"}]}
        ctx = _ctx(task="总结这份文件", extra_params=ep)
        self.agent._pre_step_attachments(ctx)
        self.assertTrue(ctx.task.startswith("总结这份文件"))
        self.assertIn("[用户附件]", ctx.task)
        # 无附件 → task 原样
        ctx2 = _ctx(task="你好")
        self.agent._pre_step_attachments(ctx2)
        self.assertEqual(ctx2.task, "你好")


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
