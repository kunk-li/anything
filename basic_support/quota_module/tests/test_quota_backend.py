# -*- coding: utf-8 -*-
"""
QuotaGuard + StateBackend 接入测试 (Task AAA #87).

覆盖两种 backend mode:
    - InMemoryBackend  — 走 StateBackend 接口, 跟今天 in-process 行为等价
    - SqliteBackend    — 跨 Connection 共享 (模拟跨 worker 进程)

关键场景:
    - rate_per_minute 跨进程汇总限流 — 4 个 worker 各发 10 不应该总发 40
    - daily_usd_limit 跨进程累计
    - global_usd_limit 跨进程
"""
import os
import tempfile
import time
import unittest

from hooks_module import BlockedError
from quota_module import QuotaGuard
from state_backend_module import InMemoryBackend, SqliteBackend


class _BaseQuotaBackendTests:
    """两种 backend 共享的合约测试."""

    def make_backend(self):
        raise NotImplementedError

    def setUp(self):
        self.backend = self.make_backend()

    def tearDown(self):
        try:
            self.backend.close()
        except Exception:
            pass

    # ---------- rate limit ----------
    def test_rate_limit_not_set_passes(self):
        guard = QuotaGuard(rate_per_minute=None, backend=self.backend)
        for _ in range(100):
            guard.check_and_record("t1")  # 不应抛

    def test_rate_limit_blocks_when_exceeded(self):
        guard = QuotaGuard(rate_per_minute=3, backend=self.backend)
        guard.check_and_record("t1")
        guard.check_and_record("t1")
        guard.check_and_record("t1")
        # 第 4 次应被拒
        with self.assertRaises(BlockedError) as cm:
            guard.check_and_record("t1")
        self.assertEqual(cm.exception.code, "RATE_LIMITED")
        self.assertIn("tenant_id", cm.exception.details)

    def test_rate_limit_per_tenant_isolated(self):
        """不同 tenant 计数互不影响."""
        guard = QuotaGuard(rate_per_minute=2, backend=self.backend)
        guard.check_and_record("a"); guard.check_and_record("a")
        # tenant a 触顶但 b 可以
        guard.check_and_record("b"); guard.check_and_record("b")
        with self.assertRaises(BlockedError):
            guard.check_and_record("a")
        with self.assertRaises(BlockedError):
            guard.check_and_record("b")

    # ---------- daily USD ----------
    def test_daily_usd_blocks_when_exceeded(self):
        guard = QuotaGuard(daily_usd_limit=1.0, backend=self.backend)
        # record_cost 持续累计
        guard.record_cost("t1", 0.5)
        guard.record_cost("t1", 0.4)
        # 0.9 + 0.5 > 1.0 → 阻断
        with self.assertRaises(BlockedError) as cm:
            guard.check_and_record("t1", cost_usd=0.5)
        self.assertEqual(cm.exception.code, "QUOTA_EXCEEDED")
        self.assertAlmostEqual(cm.exception.details["daily_usd_used"], 0.9, places=4)

    def test_daily_usd_per_tenant_isolated(self):
        guard = QuotaGuard(daily_usd_limit=1.0, backend=self.backend)
        guard.record_cost("a", 0.95)
        # tenant a 几乎触顶, tenant b 全空 — b 仍可
        guard.check_and_record("b", cost_usd=0.5)  # 不抛
        with self.assertRaises(BlockedError):
            guard.check_and_record("a", cost_usd=0.2)

    # ---------- global USD ----------
    def test_global_usd_blocks_when_exceeded(self):
        guard = QuotaGuard(global_usd_limit=2.0, backend=self.backend)
        guard.record_cost("a", 0.9)
        guard.record_cost("b", 0.8)
        # 1.7 + 0.5 > 2.0 → 阻断
        with self.assertRaises(BlockedError) as cm:
            guard.check_and_record("c", cost_usd=0.5)
        self.assertEqual(cm.exception.code, "QUOTA_EXCEEDED")

    # ---------- snapshot ----------
    def test_snapshot_shape(self):
        guard = QuotaGuard(
            daily_usd_limit=10.0,
            global_usd_limit=100.0,
            rate_per_minute=5,
            backend=self.backend,
        )
        guard.record_cost("t1", 1.5)
        guard.check_and_record("t1")
        snap = guard.snapshot()
        self.assertEqual(snap["daily_usd_limit"], 10.0)
        self.assertEqual(snap["global_usd_limit"], 100.0)
        self.assertEqual(snap["rate_per_minute"], 5)
        self.assertAlmostEqual(snap["daily_usd_used_by_tenant"]["t1"], 1.5, places=4)
        self.assertAlmostEqual(snap["global_usd_used"], 1.5, places=4)
        self.assertEqual(snap["current_rate_window_size"]["t1"], 1)

    # ---------- reset ----------
    def test_reset_clears_state(self):
        guard = QuotaGuard(rate_per_minute=2, daily_usd_limit=5.0, backend=self.backend)
        guard.check_and_record("t1")
        guard.record_cost("t1", 2.0)
        guard.reset()
        snap = guard.snapshot()
        self.assertEqual(snap["daily_usd_used_by_tenant"], {})
        self.assertEqual(snap["global_usd_used"], 0)
        # rate window 也清了 — 现在又能发 2 个
        guard.check_and_record("t1"); guard.check_and_record("t1")


class TestQuotaInMemoryBackend(_BaseQuotaBackendTests, unittest.TestCase):
    def make_backend(self):
        return InMemoryBackend()


class TestQuotaSqliteBackend(_BaseQuotaBackendTests, unittest.TestCase):
    def make_backend(self):
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        return SqliteBackend(path=self._db_path)

    def tearDown(self):
        super().tearDown()
        try:
            os.unlink(self._db_path)
            for suffix in ("-wal", "-shm"):
                if os.path.exists(self._db_path + suffix):
                    os.unlink(self._db_path + suffix)
        except Exception:
            pass


class TestSqliteCrossWorkerLimits(unittest.TestCase):
    """模拟跨 worker 限流: 同一份 SqliteBackend 文件, 两个 QuotaGuard 实例
    应该看见共享的 rate / daily / global counters."""

    def test_rate_limit_shared_across_workers(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            # worker A 用一份 backend
            backend_a = SqliteBackend(path=path)
            guard_a = QuotaGuard(rate_per_minute=4, backend=backend_a)
            guard_a.check_and_record("t1"); guard_a.check_and_record("t1")
            backend_a.close()

            # worker B 重连
            backend_b = SqliteBackend(path=path)
            guard_b = QuotaGuard(rate_per_minute=4, backend=backend_b)
            guard_b.check_and_record("t1"); guard_b.check_and_record("t1")
            # 4 个 quota 都用完了; 第 5 次 (无论哪个 worker) 都该阻断
            with self.assertRaises(BlockedError):
                guard_b.check_and_record("t1")
            backend_b.close()
        finally:
            try:
                os.unlink(path)
                for suffix in ("-wal", "-shm"):
                    if os.path.exists(path + suffix):
                        os.unlink(path + suffix)
            except Exception:
                pass

    def test_daily_usd_shared_across_workers(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            backend_a = SqliteBackend(path=path)
            guard_a = QuotaGuard(daily_usd_limit=5.0, backend=backend_a)
            guard_a.record_cost("t1", 3.0)
            backend_a.close()

            backend_b = SqliteBackend(path=path)
            guard_b = QuotaGuard(daily_usd_limit=5.0, backend=backend_b)
            guard_b.record_cost("t1", 1.5)
            # 共 4.5 USD; +0.6 > 5.0 应阻断
            with self.assertRaises(BlockedError):
                guard_b.check_and_record("t1", cost_usd=0.6)
            backend_b.close()
        finally:
            try:
                os.unlink(path)
                for suffix in ("-wal", "-shm"):
                    if os.path.exists(path + suffix):
                        os.unlink(path + suffix)
            except Exception:
                pass


class TestLegacyModeUnchanged(unittest.TestCase):
    """backend=None (默认) 路径完全不动 — 回归保护."""

    def test_default_no_backend_rate_limit(self):
        guard = QuotaGuard(rate_per_minute=2)  # 没传 backend
        guard.check_and_record("t1"); guard.check_and_record("t1")
        with self.assertRaises(BlockedError):
            guard.check_and_record("t1")

    def test_default_no_backend_daily_usd(self):
        guard = QuotaGuard(daily_usd_limit=1.0)
        guard.record_cost("t1", 0.8)
        with self.assertRaises(BlockedError):
            guard.check_and_record("t1", cost_usd=0.5)


if __name__ == "__main__":
    unittest.main()
