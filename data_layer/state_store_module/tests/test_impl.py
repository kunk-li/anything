import os

# 单独跑本测试时启用 dev mode 避免 build_basic_deps 触发 secrets fail-fast
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import tempfile
import unittest

from state_store_module.core.impl import LocalStateStore


class TestLocalStateStore(unittest.TestCase):
    """状态存储模块具体实现类的单元测试用例，覆盖核心功能场景"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="state_store_test_")
        self.state_store = LocalStateStore(store_dir=self.temp_dir)
        self.test_session_id = "test_session_001"
        self.test_state = {"task": "测试任务", "steps": [], "events": []}
        self.test_event = {
            "type": "tool_call",
            "data": {"tool": "rag_search", "result": "success"},
            "timestamp": "2026-02-27",
        }

    def tearDown(self):
        # 清理临时目录
        for root, _, files in os.walk(self.temp_dir):
            for fn in files:
                try:
                    os.remove(os.path.join(root, fn))
                except Exception:
                    pass
        try:
            os.rmdir(self.temp_dir)
        except Exception:
            pass

    def test_save_and_get_state(self):
        save_result = self.state_store.save_state(self.test_session_id, self.test_state)
        self.assertTrue(save_result)

        get_result = self.state_store.get_state(self.test_session_id)
        self.assertIsNotNone(get_result)
        self.assertEqual(get_result["task"], self.test_state["task"])
        self.assertIn("_meta", get_result)

        # 覆盖保存
        new_state = {"task": "新任务", "steps": ["a"], "events": []}
        self.assertTrue(self.state_store.save_state(self.test_session_id, new_state))
        get_result2 = self.state_store.get_state(self.test_session_id)
        self.assertEqual(get_result2["task"], "新任务")

    def test_concurrent_append_no_lost_events(self):
        """回归: 并发 append_event 不丢事件 (per-session 锁修 lost-update race)。
        无锁时多线程各读旧 state 各写回 → 后写覆盖先写, 最终事件数 < N*M。"""
        import threading
        sid = "concurrent_session"
        n_threads, per_thread = 8, 25

        def worker(tid):
            for i in range(per_thread):
                self.state_store.append_event(sid, {"type": "ev", "tid": tid, "i": i})

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        state = self.state_store.get_state(sid)
        self.assertIsNotNone(state)
        self.assertEqual(len(state.get("events", [])), n_threads * per_thread)

    def test_concurrent_readers_with_writers(self):
        """回归: 并发读 (get_state) 与写 (append_event) 不让写方崩。
        Windows 上无锁读句柄 (无 FILE_SHARE_DELETE) 会令并发写方的 os.replace
        抛 PermissionError → append_event 包成 STATE_STORE_APPEND_FAILED 上抛 + 丢事件。
        修法是 get_state 也持 per-session 锁, 与写方串行。本测试同时跑读写线程,
        断言: 全部 write 成功 (无异常) 且最终事件数恰为 writer 写入总数。"""
        import threading
        sid = "rw_session"
        n_writers, per_writer = 6, 30
        n_readers = 6
        errors = []
        stop = threading.Event()

        def writer(tid):
            try:
                for i in range(per_writer):
                    self.state_store.append_event(sid, {"type": "ev", "tid": tid, "i": i})
            except Exception as e:  # 写方崩 = 回归复现
                errors.append(e)

        def reader():
            while not stop.is_set():
                try:
                    self.state_store.get_state(sid)
                except Exception as e:
                    errors.append(e)

        rthreads = [threading.Thread(target=reader) for _ in range(n_readers)]
        wthreads = [threading.Thread(target=writer, args=(t,)) for t in range(n_writers)]
        for th in rthreads:
            th.start()
        for th in wthreads:
            th.start()
        for th in wthreads:
            th.join()
        stop.set()
        for th in rthreads:
            th.join()

        self.assertEqual(errors, [], f"并发读写下不应有异常: {errors[:3]}")
        state = self.state_store.get_state(sid)
        self.assertIsNotNone(state)
        self.assertEqual(len(state.get("events", [])), n_writers * per_writer)

    def test_append_event(self):
        self.state_store.save_state(self.test_session_id, self.test_state)
        append_result = self.state_store.append_event(self.test_session_id, self.test_event)
        self.assertTrue(append_result)

        state = self.state_store.get_state(self.test_session_id)
        self.assertEqual(len(state["events"]), 1)
        self.assertEqual(state["events"][0]["type"], self.test_event["type"])

    def test_append_event_to_new_session(self):
        # 不先save，直接append应自动创建状态
        self.assertTrue(self.state_store.append_event(self.test_session_id, self.test_event))
        state = self.state_store.get_state(self.test_session_id)
        self.assertIsNotNone(state)
        self.assertEqual(len(state["events"]), 1)

    def test_clear_state(self):
        self.state_store.save_state(self.test_session_id, self.test_state)
        clear_result = self.state_store.clear_state(self.test_session_id)
        self.assertTrue(clear_result)

        get_result = self.state_store.get_state(self.test_session_id)
        self.assertIsNone(get_result)

    def test_invalid_session_id(self):
        # save_state / clear_state 把 ValueError 包成 StateStoreException 上抛 (业务码 STATE_STORE_*)
        # get_state 拿到 ValueError 时不再 catch (避免静默吞), 仍是 ValueError
        from state_store_module.core.impl import StateStoreException

        with self.assertRaises(StateStoreException):
            self.state_store.save_state("", self.test_state)
        with self.assertRaises(ValueError):
            self.state_store.get_state("..//bad")
        with self.assertRaises(StateStoreException):
            self.state_store.clear_state("bad id")

    # ============ Task #33 PR3a: tenant_id 接口扩展 ============

    def test_tenant_id_default(self):
        from state_store_module.core.impl import LocalStateStore
        s = LocalStateStore(store_dir=self.temp_dir)
        self.assertEqual(s.tenant_id, "default")

    def test_tenant_id_specified(self):
        from state_store_module.core.impl import LocalStateStore
        s = LocalStateStore(store_dir=self.temp_dir, tenant_id="tenant-a")
        self.assertEqual(s.tenant_id, "tenant-a")

    def test_tenant_id_invalid_charset_rejected(self):
        from state_store_module.core.impl import LocalStateStore
        for bad in ("Acme Corp", "../../etc", "ab", "x" * 33):
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                LocalStateStore(store_dir=self.temp_dir, tenant_id=bad)


if __name__ == "__main__":
    unittest.main()
