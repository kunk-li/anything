# -*- coding: utf-8 -*-
"""
UsageTracker + StateBackend 接入测试 (Task XX #84).

覆盖两种 backend mode:
    - InMemoryBackend  — 走 StateBackend 接口, 跟今天 in-memory 行为等价
    - SqliteBackend    — 跨 Connection 共享 (模拟跨 worker 进程)
"""
import os
import tempfile
import unittest

from observability_module.usage_tracker import UsageTracker
from state_backend_module import InMemoryBackend, SqliteBackend


class _BaseTrackerBackendTests:
    """两种 backend 共享的合约测试."""

    def make_backend(self):
        raise NotImplementedError

    def setUp(self):
        self.backend = self.make_backend()
        self.tracker = UsageTracker(max_recent=10, backend=self.backend)

    def tearDown(self):
        try:
            self.backend.close()
        except Exception:
            pass

    def test_empty_snapshot(self):
        snap = self.tracker.snapshot()
        self.assertEqual(snap["total"]["prompt_tokens"], 0)
        self.assertEqual(snap["total"]["calls"], 0)
        self.assertEqual(snap["by_model"], {})
        self.assertEqual(snap["by_tenant"], {})
        self.assertEqual(snap["recent"], [])

    def test_single_record_accumulates(self):
        rec = self.tracker.record(
            model_name="gpt-4o-mini",
            prompt_tokens=100,
            completion_tokens=50,
            tenant_id="tenant_a",
            trace_id="tr-1",
        )
        self.assertEqual(rec["prompt_tokens"], 100)
        self.assertEqual(rec["completion_tokens"], 50)
        self.assertEqual(rec["total_tokens"], 150)
        # cost_usd 走 pricing: gpt-4o-mini prompt=0.00015, completion=0.0006
        # (100/1000)*0.00015 + (50/1000)*0.0006 = 0.000015 + 0.00003 = 0.000045
        self.assertAlmostEqual(rec["cost_usd"], 0.000045, places=8)

        snap = self.tracker.snapshot()
        self.assertEqual(snap["total"]["prompt_tokens"], 100)
        self.assertEqual(snap["total"]["completion_tokens"], 50)
        self.assertEqual(snap["total"]["calls"], 1)
        self.assertIn("gpt-4o-mini", snap["by_model"])
        self.assertEqual(snap["by_model"]["gpt-4o-mini"]["prompt_tokens"], 100)
        self.assertIn("tenant_a", snap["by_tenant"])
        self.assertEqual(snap["by_tenant"]["tenant_a"]["calls"], 1)
        self.assertEqual(len(snap["recent"]), 1)

    def test_multiple_records_aggregate(self):
        for i in range(5):
            self.tracker.record("gpt-4o", prompt_tokens=10, completion_tokens=5, tenant_id="t1")
        for i in range(3):
            self.tracker.record("claude-3-5-sonnet", prompt_tokens=20, completion_tokens=10, tenant_id="t2")

        snap = self.tracker.snapshot()
        # total: 5*15 + 3*30 = 75 + 90 = 165 total tokens
        self.assertEqual(snap["total"]["total_tokens"], 165)
        self.assertEqual(snap["total"]["calls"], 8)
        self.assertEqual(snap["by_model"]["gpt-4o"]["calls"], 5)
        self.assertEqual(snap["by_model"]["claude-3-5-sonnet"]["calls"], 3)
        self.assertEqual(snap["by_tenant"]["t1"]["calls"], 5)
        self.assertEqual(snap["by_tenant"]["t2"]["calls"], 3)
        self.assertEqual(len(snap["recent"]), 8)

    def test_recent_capped_at_max_recent(self):
        for i in range(20):
            self.tracker.record("gpt-4o", prompt_tokens=1, completion_tokens=1)
        snap = self.tracker.snapshot()
        # max_recent=10, snapshot 又只取前 20 条
        self.assertLessEqual(len(snap["recent"]), 10)

    def test_reset_clears_state(self):
        self.tracker.record("gpt-4o", prompt_tokens=100, completion_tokens=50)
        self.tracker.reset()
        snap = self.tracker.snapshot()
        self.assertEqual(snap["total"]["calls"], 0)
        self.assertEqual(snap["by_model"], {})
        self.assertEqual(snap["recent"], [])


class TestUsageTrackerInMemoryBackend(_BaseTrackerBackendTests, unittest.TestCase):
    def make_backend(self):
        return InMemoryBackend()


class TestUsageTrackerSqliteBackend(_BaseTrackerBackendTests, unittest.TestCase):
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


class TestSqliteCrossInstance(unittest.TestCase):
    """模拟跨 worker 进程: 两个 UsageTracker 实例 + 两个 SqliteBackend 实例
    共享同一个 db 文件, snapshot 累加可见."""

    def test_two_trackers_share_state(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            # worker A 记录
            backend_a = SqliteBackend(path=path)
            tracker_a = UsageTracker(backend=backend_a)
            tracker_a.record("gpt-4o", prompt_tokens=100, completion_tokens=50, tenant_id="t1")
            tracker_a.record("gpt-4o", prompt_tokens=200, completion_tokens=100, tenant_id="t1")
            backend_a.close()

            # worker B 启动, 看到 A 的累计
            backend_b = SqliteBackend(path=path)
            tracker_b = UsageTracker(backend=backend_b)
            snap_b = tracker_b.snapshot()
            self.assertEqual(snap_b["total"]["prompt_tokens"], 300)
            self.assertEqual(snap_b["total"]["calls"], 2)
            self.assertEqual(snap_b["by_model"]["gpt-4o"]["calls"], 2)
            self.assertEqual(snap_b["by_tenant"]["t1"]["calls"], 2)

            # worker B 继续记, A 重连后看见 A+B 累计
            tracker_b.record("gpt-4o", prompt_tokens=300, completion_tokens=150, tenant_id="t2")
            backend_b.close()

            backend_c = SqliteBackend(path=path)
            tracker_c = UsageTracker(backend=backend_c)
            snap_c = tracker_c.snapshot()
            self.assertEqual(snap_c["total"]["prompt_tokens"], 600)
            self.assertEqual(snap_c["total"]["calls"], 3)
            self.assertIn("t1", snap_c["by_tenant"])
            self.assertIn("t2", snap_c["by_tenant"])
            backend_c.close()
        finally:
            try:
                os.unlink(path)
                for suffix in ("-wal", "-shm"):
                    if os.path.exists(path + suffix):
                        os.unlink(path + suffix)
            except Exception:
                pass


class TestLegacyModeUnchanged(unittest.TestCase):
    """backend=None (默认) 时行为跟今天完全一致 — 回归保护."""

    def test_default_no_backend_works_as_before(self):
        tracker = UsageTracker()  # 没传 backend
        tracker.record("gpt-4o", prompt_tokens=100, completion_tokens=50)
        snap = tracker.snapshot()
        self.assertEqual(snap["total"]["prompt_tokens"], 100)
        self.assertEqual(snap["total"]["calls"], 1)


if __name__ == "__main__":
    unittest.main()
