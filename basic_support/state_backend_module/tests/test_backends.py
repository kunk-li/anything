# -*- coding: utf-8 -*-
"""
state_backend_module 测试 (Task TT #80).

跑 InMemoryBackend + SqliteBackend 同一套合约测试, 确保两个后端行为一致
(任意 tracker 切换实现时 0 修改). RedisBackend 因为是 stub 不测.
"""
import os
import tempfile
import threading
import unittest

from state_backend_module import StateBackend, InMemoryBackend, SqliteBackend


class _BackendContractMixin:
    """跑同一套契约测试 — InMemoryBackend / SqliteBackend 共享."""

    def make_backend(self) -> StateBackend:
        raise NotImplementedError

    def setUp(self):
        self.b = self.make_backend()

    def tearDown(self):
        try:
            self.b.close()
        except Exception:
            pass

    # ---------- kv ----------
    def test_get_default(self):
        self.assertEqual(self.b.get("none"), None)
        self.assertEqual(self.b.get("none", default=42), 42)

    def test_set_get_roundtrip(self):
        self.b.set("hello", "world")
        self.assertEqual(self.b.get("hello"), "world")
        self.b.set("nested", {"k": [1, 2, 3]})
        self.assertEqual(self.b.get("nested"), {"k": [1, 2, 3]})

    def test_set_overwrite(self):
        self.b.set("k", 1)
        self.b.set("k", 2)
        self.assertEqual(self.b.get("k"), 2)

    # ---------- counter ----------
    def test_incr_from_zero(self):
        self.assertEqual(self.b.incr("counter"), 1.0)
        self.assertEqual(self.b.incr("counter"), 2.0)

    def test_incr_with_delta(self):
        self.assertEqual(self.b.incr("counter", delta=5), 5.0)
        self.assertEqual(self.b.incr("counter", delta=2.5), 7.5)

    def test_incr_after_set(self):
        self.b.set("counter", 10.0)
        self.assertEqual(self.b.incr("counter"), 11.0)

    # ---------- list ----------
    def test_list_append_no_maxlen(self):
        for i in range(5):
            self.b.list_append("L", i)
        self.assertEqual(self.b.list_get("L"), [0, 1, 2, 3, 4])

    def test_list_append_with_maxlen(self):
        for i in range(10):
            self.b.list_append("L", i, maxlen=3)
        # 后 3 个保留
        self.assertEqual(self.b.list_get("L"), [7, 8, 9])

    def test_list_get_limit(self):
        for i in range(5):
            self.b.list_append("L", i)
        self.assertEqual(self.b.list_get("L", limit=2), [3, 4])

    def test_list_get_missing(self):
        self.assertEqual(self.b.list_get("missing"), [])

    # ---------- clear ----------
    def test_clear_all(self):
        self.b.set("a", 1); self.b.set("b", 2); self.b.list_append("L", "x")
        self.b.clear()
        self.assertIsNone(self.b.get("a"))
        self.assertIsNone(self.b.get("b"))
        self.assertEqual(self.b.list_get("L"), [])

    def test_clear_prefix(self):
        self.b.set("usage:total", 10)
        self.b.set("usage:tenant_a", 5)
        self.b.set("health:gpt-4", "up")
        self.b.list_append("usage:recent", "call_1")
        self.b.clear(key_prefix="usage:")
        self.assertIsNone(self.b.get("usage:total"))
        self.assertIsNone(self.b.get("usage:tenant_a"))
        self.assertEqual(self.b.list_get("usage:recent"), [])
        # health 没被清
        self.assertEqual(self.b.get("health:gpt-4"), "up")

    # ---------- 线程安全 ----------
    def test_concurrent_incr(self):
        self.b.set("k", 0)
        def worker():
            for _ in range(50):
                self.b.incr("k")
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        # 4 worker × 50 incr = 200 (从 set('k', 0) 起算, incr 把它读为 0.0 起累加)
        self.assertEqual(self.b.get("k"), 200.0)

    # ---------- transaction (原子复合更新) ----------
    def test_transaction_basic_ops(self):
        with self.b.transaction():
            self.b.set("a", 1)
            self.b.incr("c", 5)
            self.b.list_append("L", "x")
        self.assertEqual(self.b.get("a"), 1)
        self.assertEqual(self.b.get("c"), 5.0)
        self.assertEqual(self.b.list_get("L"), ["x"])

    def test_transaction_reentrant(self):
        # transaction() 块内再开 transaction() 不应死锁 / 嵌套事务报错
        with self.b.transaction():
            self.b.incr("c", 1)
            with self.b.transaction():
                self.b.incr("c", 1)
        self.assertEqual(self.b.get("c"), 2.0)

    def test_transaction_atomic_read_modify_write(self):
        """事务内的 read-modify-write 不被并发 writer 插入 → 不丢更新.

        每个 worker 在一个 transaction() 内做 get→+1→set; 没有事务保护时
        get 与 set 之间会被别的 worker 插入, 终值 < 期望.
        """
        self.b.set("rmw", 0)
        def worker():
            for _ in range(50):
                with self.b.transaction():
                    cur = int(self.b.get("rmw", 0) or 0)
                    self.b.set("rmw", cur + 1)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(self.b.get("rmw"), 200)


class TestInMemoryBackend(_BackendContractMixin, unittest.TestCase):
    def make_backend(self):
        return InMemoryBackend()


class TestSqliteBackend(_BackendContractMixin, unittest.TestCase):
    def make_backend(self):
        # 临时文件 — 测试结束 tearDown 通过 close() 释放句柄, 文件在系统 tmp 自动清理
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        return SqliteBackend(path=self._db_path)

    def tearDown(self):
        super().tearDown()
        try:
            os.unlink(self._db_path)
            # WAL 模式留下 -wal / -shm 文件, 顺手清掉
            for suffix in ("-wal", "-shm"):
                if os.path.exists(self._db_path + suffix):
                    os.unlink(self._db_path + suffix)
        except Exception:
            pass


class TestSqliteCrossInstance(unittest.TestCase):
    """SqliteBackend 跨 Connection 实例时共享同一份文件 (模拟跨进程)."""

    def test_two_instances_same_db_share_state(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            b1 = SqliteBackend(path=path)
            b1.set("shared", "from_b1")
            b1.incr("counter", delta=5)
            b1.list_append("L", "item1")
            b1.close()

            b2 = SqliteBackend(path=path)
            self.assertEqual(b2.get("shared"), "from_b1")
            self.assertEqual(b2.get("counter"), 5.0)
            self.assertEqual(b2.list_get("L"), ["item1"])

            b2.incr("counter", delta=3)
            b2.close()

            b3 = SqliteBackend(path=path)
            self.assertEqual(b3.get("counter"), 8.0)
            b3.close()
        finally:
            try:
                os.unlink(path)
                for suffix in ("-wal", "-shm"):
                    if os.path.exists(path + suffix):
                        os.unlink(path + suffix)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
