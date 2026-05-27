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
        with self.assertRaises(ValueError):
            self.state_store.save_state("", self.test_state)
        with self.assertRaises(ValueError):
            self.state_store.get_state("..//bad")
        with self.assertRaises(ValueError):
            self.state_store.clear_state("bad id")

    # ============ Task #33 PR3a: tenant_id 接口扩展 ============

    def test_tenant_id_default(self):
        from state_store_module.core.impl import LocalStateStore
        s = LocalStateStore(store_dir=self.store_dir)
        self.assertEqual(s.tenant_id, "default")

    def test_tenant_id_specified(self):
        from state_store_module.core.impl import LocalStateStore
        s = LocalStateStore(store_dir=self.store_dir, tenant_id="tenant-a")
        self.assertEqual(s.tenant_id, "tenant-a")

    def test_tenant_id_invalid_charset_rejected(self):
        from state_store_module.core.impl import LocalStateStore
        for bad in ("Acme Corp", "../../etc", "ab", "x" * 33):
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                LocalStateStore(store_dir=self.store_dir, tenant_id=bad)


if __name__ == "__main__":
    unittest.main()
