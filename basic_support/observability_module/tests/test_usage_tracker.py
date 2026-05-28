# -*- coding: utf-8 -*-
"""
UsageTracker 单元测试 (Task Y #59)

覆盖:
    - record() 累加 total / by_model / by_tenant
    - estimate_cost() 用 pricing 表估值
    - 缺失模型 -> cost = 0 (保守)
    - snapshot 结构 + recent 倒序
    - reset / 单例 行为
    - 环境变量 ANYTHING_LLM_PRICING 覆盖默认 pricing
"""

import json
import os
import unittest

from observability_module import (
    UsageTracker,
    get_usage_tracker,
    reset_usage_tracker,
)


class TestUsageTracker(unittest.TestCase):
    def setUp(self):
        os.environ.pop("ANYTHING_LLM_PRICING", None)
        reset_usage_tracker()

    def tearDown(self):
        os.environ.pop("ANYTHING_LLM_PRICING", None)
        reset_usage_tracker()

    def test_record_accumulates_total(self):
        tracker = UsageTracker()
        tracker.record(model_name="gpt-4o-mini", prompt_tokens=100, completion_tokens=50)
        tracker.record(model_name="gpt-4o-mini", prompt_tokens=200, completion_tokens=80)
        snap = tracker.snapshot()
        self.assertEqual(snap["total"]["prompt_tokens"], 300)
        self.assertEqual(snap["total"]["completion_tokens"], 130)
        self.assertEqual(snap["total"]["total_tokens"], 430)
        self.assertEqual(snap["total"]["calls"], 2)
        self.assertGreater(snap["total"]["cost_usd"], 0)

    def test_estimate_cost_per_model(self):
        tracker = UsageTracker()
        # gpt-4o-mini: prompt=$0.00015, completion=$0.0006 per 1K
        cost = tracker.estimate_cost("gpt-4o-mini", prompt_tokens=1000, completion_tokens=1000)
        self.assertAlmostEqual(cost, 0.00015 + 0.0006, places=6)

    def test_unknown_model_zero_cost(self):
        tracker = UsageTracker()
        cost = tracker.estimate_cost("never-heard-of-it", 1000, 1000)
        self.assertEqual(cost, 0.0)

    def test_by_model_breakdown(self):
        tracker = UsageTracker()
        tracker.record(model_name="gpt-4o-mini", prompt_tokens=100, completion_tokens=50)
        tracker.record(model_name="qwen-turbo", prompt_tokens=200, completion_tokens=80)
        snap = tracker.snapshot()
        self.assertIn("gpt-4o-mini", snap["by_model"])
        self.assertIn("qwen-turbo", snap["by_model"])
        self.assertEqual(snap["by_model"]["gpt-4o-mini"]["calls"], 1)
        self.assertEqual(snap["by_model"]["qwen-turbo"]["calls"], 1)

    def test_by_tenant_breakdown(self):
        tracker = UsageTracker()
        tracker.record(model_name="gpt-4o-mini", prompt_tokens=100, completion_tokens=10, tenant_id="t1")
        tracker.record(model_name="gpt-4o-mini", prompt_tokens=200, completion_tokens=20, tenant_id="t2")
        tracker.record(model_name="gpt-4o-mini", prompt_tokens=50, completion_tokens=5)  # 默认 default
        snap = tracker.snapshot()
        self.assertEqual(snap["by_tenant"]["t1"]["calls"], 1)
        self.assertEqual(snap["by_tenant"]["t2"]["prompt_tokens"], 200)
        self.assertEqual(snap["by_tenant"]["default"]["prompt_tokens"], 50)

    def test_recent_is_lifo_with_cap(self):
        tracker = UsageTracker(max_recent=3)
        for i in range(5):
            tracker.record(model_name=f"m{i}", prompt_tokens=i)
        recent = tracker.snapshot()["recent"]
        self.assertEqual(len(recent), 3)
        # deque(maxlen=3) + appendleft → 保留 m4, m3, m2 (最新在最前)
        models = [r["model"] for r in recent]
        self.assertEqual(models, ["m4", "m3", "m2"])

    def test_reset_clears_state(self):
        tracker = UsageTracker()
        tracker.record(model_name="x", prompt_tokens=10)
        tracker.reset()
        snap = tracker.snapshot()
        self.assertEqual(snap["total"]["calls"], 0)
        self.assertEqual(snap["by_model"], {})
        self.assertEqual(snap["recent"], [])

    def test_env_pricing_override(self):
        os.environ["ANYTHING_LLM_PRICING"] = json.dumps(
            {"my-model": {"prompt": 0.99, "completion": 1.99}}
        )
        tracker = UsageTracker()
        cost = tracker.estimate_cost("my-model", prompt_tokens=1000, completion_tokens=1000)
        self.assertAlmostEqual(cost, 0.99 + 1.99, places=4)

    def test_explicit_pricing_override(self):
        tracker = UsageTracker(pricing={"another-model": {"prompt": 0.1, "completion": 0.2}})
        cost = tracker.estimate_cost("another-model", 1000, 2000)
        self.assertAlmostEqual(cost, 0.1 + 2 * 0.2, places=4)

    def test_singleton_get_and_reset(self):
        a = get_usage_tracker()
        b = get_usage_tracker()
        self.assertIs(a, b)
        reset_usage_tracker()
        c = get_usage_tracker()
        self.assertIsNot(a, c)

    def test_explicit_cost_overrides_estimate(self):
        """传 cost_usd 显式值时跟踪器尊重它, 不重新估算"""
        tracker = UsageTracker()
        rec = tracker.record(
            model_name="gpt-4o-mini",
            prompt_tokens=1000,
            completion_tokens=1000,
            cost_usd=42.0,
        )
        self.assertEqual(rec["cost_usd"], 42.0)
        snap = tracker.snapshot()
        self.assertEqual(snap["total"]["cost_usd"], 42.0)


if __name__ == "__main__":
    unittest.main()
