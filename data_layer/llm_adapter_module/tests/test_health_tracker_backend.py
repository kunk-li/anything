# -*- coding: utf-8 -*-
"""
ModelHealthTracker + StateBackend 接入测试 (Task BBB #88).

关键场景:
    - worker A 标记模型 X unhealthy 后, worker B 通过同一 SqliteBackend 看到 X unhealthy
      → Task HH fallback chain 跨 worker 一致
    - 跨 worker 失败计数累计
    - 冷却窗口 / probation 状态机
"""
import os
import tempfile
import threading
import time
import unittest

from llm_adapter_module.utils.health_tracker import ModelHealthTracker
from state_backend_module import InMemoryBackend, SqliteBackend


class _BaseHealthBackendTests:
    """两种 backend 共享的合约测试."""

    def make_backend(self):
        raise NotImplementedError

    def setUp(self):
        self.backend = self.make_backend()
        # threshold=3, cooldown=2s 方便测冷却恢复
        self.tracker = ModelHealthTracker(
            fail_threshold=3, cooldown_seconds=2, backend=self.backend,
        )

    def tearDown(self):
        try:
            self.backend.close()
        except Exception:
            pass

    def test_new_model_is_healthy(self):
        self.assertTrue(self.tracker.is_available("gpt-4o"))

    def test_record_success_keeps_healthy(self):
        self.tracker.record_success("gpt-4o")
        self.assertTrue(self.tracker.is_available("gpt-4o"))
        snap = self.tracker.snapshot()
        self.assertEqual(snap["models"]["gpt-4o"]["state"], "healthy")
        self.assertEqual(snap["models"]["gpt-4o"]["total_calls"], 1)

    def test_below_threshold_stays_healthy(self):
        self.tracker.record_failure("gpt-4o", "timeout")
        self.tracker.record_failure("gpt-4o", "timeout")
        self.assertTrue(self.tracker.is_available("gpt-4o"))
        snap = self.tracker.snapshot()
        self.assertEqual(snap["models"]["gpt-4o"]["consecutive_failures"], 2)

    def test_threshold_triggers_unhealthy(self):
        for _ in range(3):
            self.tracker.record_failure("gpt-4o", "boom")
        # 3 次连失 → unhealthy
        self.assertFalse(self.tracker.is_available("gpt-4o"))
        snap = self.tracker.snapshot()
        self.assertEqual(snap["models"]["gpt-4o"]["state"], "unhealthy")
        self.assertGreater(snap["models"]["gpt-4o"]["cooldown_remaining_seconds"], 0)

    def test_consecutive_reset_on_success(self):
        self.tracker.record_failure("gpt-4o")
        self.tracker.record_failure("gpt-4o")
        self.tracker.record_success("gpt-4o")
        # success 重置 consecutive
        snap = self.tracker.snapshot()
        self.assertEqual(snap["models"]["gpt-4o"]["consecutive_failures"], 0)
        # 不足 threshold 仍 healthy
        self.tracker.record_failure("gpt-4o"); self.tracker.record_failure("gpt-4o")
        self.assertTrue(self.tracker.is_available("gpt-4o"))

    def test_cooldown_then_probation(self):
        for _ in range(3):
            self.tracker.record_failure("gpt-4o", "503")
        self.assertFalse(self.tracker.is_available("gpt-4o"))
        # 等冷却时间过 (2s)
        time.sleep(2.1)
        # 应自动进入 probation 允许试探
        self.assertTrue(self.tracker.is_available("gpt-4o"))
        snap = self.tracker.snapshot()
        self.assertEqual(snap["models"]["gpt-4o"]["state"], "probation")

    def test_probation_failure_back_to_unhealthy(self):
        for _ in range(3):
            self.tracker.record_failure("gpt-4o")
        time.sleep(2.1)
        # 触发 probation
        self.tracker.is_available("gpt-4o")
        # probation 失败 → 立刻 unhealthy 再冷却
        self.tracker.record_failure("gpt-4o", "still bad")
        self.assertFalse(self.tracker.is_available("gpt-4o"))

    def test_probation_success_back_to_healthy(self):
        for _ in range(3):
            self.tracker.record_failure("gpt-4o")
        time.sleep(2.1)
        self.tracker.is_available("gpt-4o")  # 触发 probation
        self.tracker.record_success("gpt-4o")
        snap = self.tracker.snapshot()
        self.assertEqual(snap["models"]["gpt-4o"]["state"], "healthy")

    def test_snapshot_failure_rate(self):
        self.tracker.record_success("gpt-4o")
        self.tracker.record_failure("gpt-4o")
        self.tracker.record_failure("gpt-4o")
        snap = self.tracker.snapshot()
        # 2/3 fail = 0.667
        self.assertAlmostEqual(snap["models"]["gpt-4o"]["failure_rate"], 0.667, places=2)

    def test_reset_clears(self):
        for _ in range(3):
            self.tracker.record_failure("gpt-4o")
        self.tracker.reset()
        self.assertTrue(self.tracker.is_available("gpt-4o"))
        snap = self.tracker.snapshot()
        self.assertEqual(snap["models"], {})


class TestHealthInMemoryBackend(_BaseHealthBackendTests, unittest.TestCase):
    def make_backend(self):
        return InMemoryBackend()


class TestHealthSqliteBackend(_BaseHealthBackendTests, unittest.TestCase):
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


class TestSqliteCrossWorkerHealth(unittest.TestCase):
    """关键: worker A 标记模型 X unhealthy → worker B 立刻看见 X unhealthy
    → fallback chain 跨进程一致 (Task HH 真实生效)."""

    def test_unhealthy_propagates_across_workers(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            # worker A 记 3 次失败
            backend_a = SqliteBackend(path=path)
            tracker_a = ModelHealthTracker(
                fail_threshold=3, cooldown_seconds=60, backend=backend_a,
            )
            for _ in range(3):
                tracker_a.record_failure("gpt-4o", "503")
            self.assertFalse(tracker_a.is_available("gpt-4o"))
            backend_a.close()

            # worker B 重连 — 应该立刻知道 gpt-4o 是 unhealthy
            backend_b = SqliteBackend(path=path)
            tracker_b = ModelHealthTracker(
                fail_threshold=3, cooldown_seconds=60, backend=backend_b,
            )
            self.assertFalse(tracker_b.is_available("gpt-4o"),
                             "worker B 应该看见 worker A 标的 unhealthy")
            snap = tracker_b.snapshot()
            self.assertEqual(snap["models"]["gpt-4o"]["state"], "unhealthy")
            self.assertEqual(snap["models"]["gpt-4o"]["total_failures"], 3)
            backend_b.close()
        finally:
            try:
                os.unlink(path)
                for suffix in ("-wal", "-shm"):
                    if os.path.exists(path + suffix):
                        os.unlink(path + suffix)
            except Exception:
                pass

    def test_failures_accumulate_across_workers(self):
        """worker A 1 次失败 + worker B 1 次失败 + worker C 1 次失败 (threshold=3)
        → 第 3 次后 model 被标 unhealthy, 即使每 worker 各自只看到 1 次失败."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            backend_a = SqliteBackend(path=path)
            tracker_a = ModelHealthTracker(fail_threshold=3, backend=backend_a)
            tracker_a.record_failure("gpt-4o", "err_a")
            self.assertTrue(tracker_a.is_available("gpt-4o"))  # 1次, 还可用
            backend_a.close()

            backend_b = SqliteBackend(path=path)
            tracker_b = ModelHealthTracker(fail_threshold=3, backend=backend_b)
            tracker_b.record_failure("gpt-4o", "err_b")
            self.assertTrue(tracker_b.is_available("gpt-4o"))  # 2次, 还可用
            backend_b.close()

            backend_c = SqliteBackend(path=path)
            tracker_c = ModelHealthTracker(fail_threshold=3, backend=backend_c)
            tracker_c.record_failure("gpt-4o", "err_c")
            # 第 3 次失败 — 跨进程累计达到 threshold → unhealthy
            self.assertFalse(tracker_c.is_available("gpt-4o"))
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
    def test_default_no_backend(self):
        tracker = ModelHealthTracker(fail_threshold=2, cooldown_seconds=10)
        tracker.record_failure("gpt-4o"); tracker.record_failure("gpt-4o")
        self.assertFalse(tracker.is_available("gpt-4o"))


class _ConcurrentConsistencyMixin:
    """并发 record_success / record_failure 不能把共享 health state 写花.

    回归: 修复前 backend 模式下每个 record_* 走多个独立 StateBackend op (incr/get/set),
    op 间没有事务包裹, 并发时 state 与 consecutive_failures 会写成互相矛盾的值
    (例: state='unhealthy' 却 consecutive_failures=0). 修复后整组更新在
    backend.transaction() 内原子提交, 终态始终自洽.
    """

    def make_backend(self):
        raise NotImplementedError

    def tearDown(self):
        try:
            self.backend.close()
        except Exception:
            pass

    def test_concurrent_success_failure_state_consistent(self):
        self.backend = self.make_backend()
        # threshold 高到不会因正常累计触发, 隔离 "并发写花" 这一个变量
        tracker = ModelHealthTracker(
            fail_threshold=10000, cooldown_seconds=300, backend=self.backend,
        )
        model = "gpt-4o"
        barrier = threading.Barrier(2)

        def hammer(fn):
            barrier.wait()
            for _ in range(200):
                fn(model)

        t_ok = threading.Thread(target=hammer, args=(tracker.record_success,))
        t_bad = threading.Thread(
            target=hammer, args=(lambda m: tracker.record_failure(m, "boom"),)
        )
        t_ok.start(); t_bad.start()
        t_ok.join(); t_bad.join()

        snap = tracker.snapshot()["models"][model]
        # 计数器不丢更新: 200 成功 + 200 失败 = 400 total_calls, 200 total_failures
        self.assertEqual(snap["total_calls"], 400)
        self.assertEqual(snap["total_failures"], 200)
        # state 与 consecutive_failures 必须自洽 — 不能出现 unhealthy 却 0 连失,
        # 也不能 healthy 却连失达到/超过阈值 (这里阈值很高, 真正校验的是 state 不被写花).
        state = snap["state"]
        consec = snap["consecutive_failures"]
        self.assertIn(state, ("healthy", "unhealthy", "probation"))
        if state == "unhealthy":
            self.assertGreaterEqual(
                consec, tracker._fail_threshold,
                "unhealthy 必须由连失达到阈值或 probation 探针失败造成, 不应凭空出现",
            )


class TestConcurrentInMemory(_ConcurrentConsistencyMixin, unittest.TestCase):
    def make_backend(self):
        return InMemoryBackend()


class TestConcurrentSqlite(_ConcurrentConsistencyMixin, unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
