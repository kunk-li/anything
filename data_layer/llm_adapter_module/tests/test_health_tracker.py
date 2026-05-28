# -*- coding: utf-8 -*-
"""
ModelHealthTracker 单元测试 (Task HH #68)
"""

import time
import unittest

from llm_adapter_module.utils import (
    ModelHealthTracker,
    configure_health_tracker,
    get_health_tracker,
    reset_health_tracker,
)


class TestHealthTracker(unittest.TestCase):
    def setUp(self):
        reset_health_tracker()

    def tearDown(self):
        reset_health_tracker()

    def test_new_model_is_healthy(self):
        h = ModelHealthTracker()
        self.assertTrue(h.is_available("gpt-4o"))

    def test_failure_under_threshold_stays_healthy(self):
        h = ModelHealthTracker(fail_threshold=3)
        h.record_failure("gpt-4o")
        h.record_failure("gpt-4o")
        self.assertTrue(h.is_available("gpt-4o"))

    def test_failure_at_threshold_marks_unhealthy(self):
        h = ModelHealthTracker(fail_threshold=3, cooldown_seconds=60)
        for _ in range(3):
            h.record_failure("gpt-4o")
        self.assertFalse(h.is_available("gpt-4o"))

    def test_success_resets_consecutive_failures(self):
        h = ModelHealthTracker(fail_threshold=3)
        h.record_failure("gpt-4o")
        h.record_failure("gpt-4o")
        h.record_success("gpt-4o")
        h.record_failure("gpt-4o")
        h.record_failure("gpt-4o")
        # 现在只有 2 个连续失败 (success 重置了计数), 仍 healthy
        self.assertTrue(h.is_available("gpt-4o"))

    def test_cooldown_then_probation(self):
        """unhealthy 后等冷却 → 自动进入 probation, is_available 返回 True"""
        h = ModelHealthTracker(fail_threshold=2, cooldown_seconds=1)
        h.record_failure("x")
        h.record_failure("x")
        self.assertFalse(h.is_available("x"))
        time.sleep(1.1)
        # 冷却过了 → probation, 允许 1 次试探
        self.assertTrue(h.is_available("x"))
        snap = h.snapshot()
        self.assertEqual(snap["models"]["x"]["state"], "probation")

    def test_probation_success_back_to_healthy(self):
        h = ModelHealthTracker(fail_threshold=1, cooldown_seconds=1)
        h.record_failure("x")
        time.sleep(1.1)
        # 进入 probation
        self.assertTrue(h.is_available("x"))
        # probation 期间成功 → 回 healthy
        h.record_success("x")
        snap = h.snapshot()
        self.assertEqual(snap["models"]["x"]["state"], "healthy")
        self.assertEqual(snap["models"]["x"]["consecutive_failures"], 0)

    def test_probation_failure_back_to_unhealthy(self):
        h = ModelHealthTracker(fail_threshold=1, cooldown_seconds=1)
        h.record_failure("x")
        time.sleep(1.1)
        self.assertTrue(h.is_available("x"))  # probation
        h.record_failure("x")  # 探针失败
        snap = h.snapshot()
        self.assertEqual(snap["models"]["x"]["state"], "unhealthy")
        # 又得等下一轮冷却
        self.assertFalse(h.is_available("x"))

    def test_multiple_models_isolated(self):
        h = ModelHealthTracker(fail_threshold=2, cooldown_seconds=60)
        h.record_failure("a")
        h.record_failure("a")
        # a unhealthy, b 健康
        self.assertFalse(h.is_available("a"))
        self.assertTrue(h.is_available("b"))

    def test_snapshot_structure(self):
        h = ModelHealthTracker(fail_threshold=2, cooldown_seconds=60)
        h.record_failure("a", error="timeout")
        h.record_success("a")
        h.record_failure("b")
        snap = h.snapshot()
        self.assertEqual(snap["fail_threshold"], 2)
        self.assertEqual(snap["cooldown_seconds"], 60)
        self.assertIn("a", snap["models"])
        self.assertIn("b", snap["models"])
        self.assertEqual(snap["models"]["a"]["state"], "healthy")
        self.assertEqual(snap["models"]["a"]["total_failures"], 1)
        self.assertEqual(snap["models"]["a"]["total_calls"], 2)
        self.assertEqual(snap["models"]["a"]["failure_rate"], 0.5)

    def test_empty_model_name_safe(self):
        h = ModelHealthTracker()
        self.assertFalse(h.is_available(""))
        h.record_failure("")  # 不抛
        h.record_success("")  # 不抛

    def test_singleton(self):
        a = get_health_tracker()
        b = get_health_tracker()
        self.assertIs(a, b)
        reset_health_tracker()
        c = get_health_tracker()
        self.assertIsNot(a, c)

    def test_configure_health_tracker_swaps_singleton(self):
        configure_health_tracker(fail_threshold=10, cooldown_seconds=30)
        g = get_health_tracker()
        self.assertEqual(g._fail_threshold, 10)
        self.assertEqual(g._cooldown_seconds, 30)


if __name__ == "__main__":
    unittest.main()
