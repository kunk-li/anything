# -*- coding: utf-8 -*-
"""
Quota / Rate-limit 单元测试 (Task BB #62)
"""

import time
import unittest

from common_utils_module import (
    BlockedError,
    QuotaGuard,
    configure_quota,
    get_quota_guard,
    install_quota_hooks,
    reset_hook_registry,
    reset_quota_guard,
    get_hook_registry,
)


class TestQuotaGuard(unittest.TestCase):
    def setUp(self):
        reset_quota_guard()
        reset_hook_registry()

    def tearDown(self):
        reset_quota_guard()
        reset_hook_registry()

    # ---------------- rate limit ----------------
    def test_rate_limit_blocks_after_threshold(self):
        q = QuotaGuard(rate_per_minute=3)
        for _ in range(3):
            q.check_and_record(tenant_id="t1")
        with self.assertRaises(BlockedError) as cm:
            q.check_and_record(tenant_id="t1")
        self.assertEqual(cm.exception.code, "RATE_LIMITED")
        self.assertIn("retry_after_seconds", cm.exception.details)

    def test_rate_limit_per_tenant_isolated(self):
        q = QuotaGuard(rate_per_minute=2)
        q.check_and_record("a")
        q.check_and_record("a")
        # t=a 已满, 但 t=b 不影响
        q.check_and_record("b")
        q.check_and_record("b")
        with self.assertRaises(BlockedError):
            q.check_and_record("a")

    def test_rate_limit_disabled_when_none(self):
        q = QuotaGuard(rate_per_minute=None)
        # 调 1000 次都不抛
        for _ in range(1000):
            q.check_and_record("x")

    def test_rate_limit_disabled_when_zero(self):
        q = QuotaGuard(rate_per_minute=0)
        for _ in range(100):
            q.check_and_record("x")

    # ---------------- daily USD ----------------
    def test_daily_usd_blocks(self):
        q = QuotaGuard(daily_usd_limit=1.0)
        q.check_and_record("t1", cost_usd=0.5)
        q.record_cost("t1", cost_usd=0.5)  # 模拟事后补登
        q.check_and_record("t1", cost_usd=0.3)
        q.record_cost("t1", cost_usd=0.3)
        with self.assertRaises(BlockedError) as cm:
            q.check_and_record("t1", cost_usd=0.5)  # 0.8 + 0.5 > 1.0
        self.assertEqual(cm.exception.code, "QUOTA_EXCEEDED")

    def test_daily_usd_per_tenant_isolated(self):
        q = QuotaGuard(daily_usd_limit=1.0)
        q.record_cost("a", 0.9)
        # tenant b 没用过, 应能跑
        q.check_and_record("b", cost_usd=0.5)
        # tenant a 接近上限, 再来 0.5 应该被拒
        with self.assertRaises(BlockedError):
            q.check_and_record("a", cost_usd=0.5)

    def test_daily_usd_disabled_when_none(self):
        q = QuotaGuard(daily_usd_limit=None)
        q.check_and_record("x", cost_usd=99999.0)

    # ---------------- global USD ----------------
    def test_global_usd_blocks(self):
        q = QuotaGuard(global_usd_limit=2.0)
        q.record_cost("t1", 1.0)
        q.record_cost("t2", 0.5)
        with self.assertRaises(BlockedError) as cm:
            q.check_and_record("t3", cost_usd=1.0)  # 1.5+1.0 > 2.0
        self.assertEqual(cm.exception.code, "QUOTA_EXCEEDED")

    # ---------------- snapshot ----------------
    def test_snapshot_structure(self):
        q = QuotaGuard(daily_usd_limit=10.0, rate_per_minute=20)
        q.check_and_record("t1")
        q.record_cost("t1", 0.5)
        snap = q.snapshot()
        self.assertEqual(snap["daily_usd_limit"], 10.0)
        self.assertEqual(snap["rate_per_minute"], 20)
        self.assertIn("t1", snap["daily_usd_used_by_tenant"])
        self.assertAlmostEqual(snap["daily_usd_used_by_tenant"]["t1"], 0.5)

    def test_reset(self):
        q = QuotaGuard(daily_usd_limit=10.0, rate_per_minute=3)
        for _ in range(3):
            q.check_and_record("x")
        q.record_cost("x", 5.0)
        q.reset()
        # 重置后可以再来一次
        q.check_and_record("x", cost_usd=1.0)


class TestInstallQuotaHooks(unittest.TestCase):
    def setUp(self):
        reset_quota_guard()
        reset_hook_registry()

    def tearDown(self):
        reset_quota_guard()
        reset_hook_registry()

    def test_install_registers_hooks(self):
        install_quota_hooks(rate_per_minute=2)
        counts = get_hook_registry().count()
        self.assertGreaterEqual(counts["pre_llm_call"], 1)
        self.assertGreaterEqual(counts["pre_tool_call"], 1)
        self.assertGreaterEqual(counts["post_llm_call"], 1)

    def test_pre_llm_hook_blocks(self):
        install_quota_hooks(rate_per_minute=1)
        reg = get_hook_registry()
        # 第一次 OK
        reg.fire("pre_llm_call", "hello", "gpt-4o-mini", {"tenant_id": "t1"})
        # 第二次应该被 quota hook 抛 BlockedError
        with self.assertRaises(BlockedError) as cm:
            reg.fire("pre_llm_call", "again", "gpt-4o-mini", {"tenant_id": "t1"})
        self.assertEqual(cm.exception.code, "RATE_LIMITED")

    def test_pre_tool_hook_also_blocks(self):
        install_quota_hooks(rate_per_minute=1)
        reg = get_hook_registry()
        reg.fire("pre_tool_call", "calc", {"x": 1}, {"tenant_id": "t2"})
        with self.assertRaises(BlockedError):
            reg.fire("pre_tool_call", "calc", {"x": 2}, {"tenant_id": "t2"})

    def test_configure_quota_swaps_singleton(self):
        configure_quota(rate_per_minute=10)
        g1 = get_quota_guard()
        self.assertEqual(g1.rate_per_minute, 10)
        configure_quota(rate_per_minute=20)
        g2 = get_quota_guard()
        self.assertIsNot(g1, g2)
        self.assertEqual(g2.rate_per_minute, 20)


if __name__ == "__main__":
    unittest.main()
