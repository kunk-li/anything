# -*- coding: utf-8 -*-
"""
LongTermMemoryImpl 测试 (Task DDD #90).

覆盖 add / search / mark_accessed / list / delete / prune.
跑两种 backend (InMemory + Sqlite) 同一份合约, 跨 worker 共享走一遍.
"""
import os
import tempfile
import time
import unittest

from long_term_memory_module import (
    LongTermMemoryImpl, Fact, MemoryQuery,
)
from state_backend_module import InMemoryBackend, SqliteBackend


class _BaseMemoryTests:
    """两种 backend 共享的合约测试."""

    def make_backend(self):
        raise NotImplementedError

    def setUp(self):
        self.backend = self.make_backend()
        self.memory = LongTermMemoryImpl(backend=self.backend)

    def tearDown(self):
        try:
            self.backend.close()
        except Exception:
            pass

    # ---------- add_fact 基础 ----------

    def test_add_new_fact(self):
        f = self.memory.add_fact(Fact.make(
            "用户喜欢 Python 不喜欢 JavaScript", tenant_id="t1", tags=["preference"],
        ))
        self.assertEqual(f.tenant_id, "t1")
        self.assertEqual(f.tags, ["preference"])
        self.assertGreater(len(f.content_hash), 0)
        self.assertEqual(f.access_count, 0)

    def test_add_dedup_by_content_hash(self):
        """用户的核心设计: 相同 content 不重复存, 而是 mark_accessed."""
        f1 = self.memory.add_fact(Fact.make("用户喜欢 Python", tenant_id="t1"))
        f2 = self.memory.add_fact(Fact.make("用户喜欢 Python", tenant_id="t1"))
        # 同一个 fact, bump 不新增
        self.assertEqual(f1.fact_id, f2.fact_id)
        self.assertEqual(f2.access_count, 1)
        # 第三次再 add
        f3 = self.memory.add_fact(Fact.make("用户喜欢 Python", tenant_id="t1"))
        self.assertEqual(f3.access_count, 2)

    def test_add_dedup_case_insensitive(self):
        """content_hash 走 lower().strip(), 大小写 / 前后空格 不影响判重."""
        f1 = self.memory.add_fact(Fact.make("Hello World", tenant_id="t1"))
        f2 = self.memory.add_fact(Fact.make("  hello world  ", tenant_id="t1"))
        self.assertEqual(f1.fact_id, f2.fact_id)

    def test_add_dedup_tag_merge(self):
        """重复 add 时, 新 fact 带的额外 tag 合并到已有 fact."""
        self.memory.add_fact(Fact.make("X", tenant_id="t1", tags=["a"]))
        f = self.memory.add_fact(Fact.make("X", tenant_id="t1", tags=["b", "c"]))
        self.assertEqual(set(f.tags), {"a", "b", "c"})

    def test_add_tenant_isolation(self):
        """不同 tenant 的相同 content 互不影响."""
        f1 = self.memory.add_fact(Fact.make("X", tenant_id="t1"))
        f2 = self.memory.add_fact(Fact.make("X", tenant_id="t2"))
        self.assertNotEqual(f1.fact_id, f2.fact_id)
        self.assertEqual(f1.tenant_id, "t1")
        self.assertEqual(f2.tenant_id, "t2")

    # ---------- search_facts ----------

    def test_search_exact_hash_hit(self):
        self.memory.add_fact(Fact.make("用户喜欢 Python", tenant_id="t1"))
        hits = self.memory.search_facts(
            MemoryQuery(query="用户喜欢 Python", tenant_id="t1")
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].score, 1.0)
        self.assertEqual(hits[0].reason, "content_hash_exact")

    def test_search_substring_match(self):
        self.memory.add_fact(Fact.make("用户喜欢 Python 编程", tenant_id="t1"))
        hits = self.memory.search_facts(
            MemoryQuery(query="Python", tenant_id="t1", top_k=5)
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].reason, "substring")

    def test_search_marks_accessed_on_hit(self):
        """用户设计的核心: 命中时自动 mark_accessed (access_count + 1)."""
        f = self.memory.add_fact(Fact.make("hello world", tenant_id="t1"))
        self.assertEqual(f.access_count, 0)
        self.memory.search_facts(MemoryQuery(query="hello world", tenant_id="t1"))
        # 重新读出来看
        listed = self.memory.list_facts(tenant_id="t1")
        target = [x for x in listed if x.fact_id == f.fact_id][0]
        self.assertGreater(target.access_count, 0)

    def test_search_no_match_empty(self):
        self.memory.add_fact(Fact.make("alpha", tenant_id="t1"))
        hits = self.memory.search_facts(
            MemoryQuery(query="beta_no_match", tenant_id="t1")
        )
        self.assertEqual(hits, [])

    def test_search_tag_filter(self):
        self.memory.add_fact(Fact.make("preference_fact", tenant_id="t1", tags=["preference"]))
        self.memory.add_fact(Fact.make("decision_fact", tenant_id="t1", tags=["decision"]))
        # 只查 preference tag
        hits = self.memory.search_facts(
            MemoryQuery(query="fact", tenant_id="t1", tags_filter=["preference"])
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].fact.tags, ["preference"])

    def test_search_min_confidence_filter(self):
        self.memory.add_fact(Fact.make("hi_high", tenant_id="t1", confidence=0.9))
        self.memory.add_fact(Fact.make("hi_low", tenant_id="t1", confidence=0.4))
        hits = self.memory.search_facts(
            MemoryQuery(query="hi", tenant_id="t1", min_confidence=0.5)
        )
        contents = [h.fact.content for h in hits]
        self.assertIn("hi_high", contents)
        self.assertNotIn("hi_low", contents)

    def test_search_top_k_limit(self):
        for i in range(10):
            self.memory.add_fact(Fact.make(f"fact_{i}", tenant_id="t1"))
        hits = self.memory.search_facts(
            MemoryQuery(query="fact_", tenant_id="t1", top_k=3)
        )
        self.assertLessEqual(len(hits), 3)

    # ---------- mark_accessed ----------

    def test_mark_accessed_bumps_count(self):
        f = self.memory.add_fact(Fact.make("x", tenant_id="t1"))
        self.memory.mark_accessed(f.fact_id, tenant_id="t1")
        self.memory.mark_accessed(f.fact_id, tenant_id="t1")
        facts = self.memory.list_facts("t1")
        self.assertEqual(facts[0].access_count, 2)

    def test_mark_accessed_unknown_returns_false(self):
        self.assertFalse(self.memory.mark_accessed("nonexistent", tenant_id="t1"))

    # ---------- list_facts ----------

    def test_list_sorted_by_last_accessed(self):
        f1 = self.memory.add_fact(Fact.make("old", tenant_id="t1"))
        time.sleep(0.01)
        f2 = self.memory.add_fact(Fact.make("middle", tenant_id="t1"))
        time.sleep(0.01)
        f3 = self.memory.add_fact(Fact.make("new", tenant_id="t1"))
        facts = self.memory.list_facts("t1")
        self.assertEqual(facts[0].fact_id, f3.fact_id)  # 最近 access 在最前
        self.assertEqual(facts[-1].fact_id, f1.fact_id)

    def test_list_pagination(self):
        for i in range(5):
            self.memory.add_fact(Fact.make(f"fact_{i}", tenant_id="t1"))
        page1 = self.memory.list_facts("t1", limit=2, offset=0)
        page2 = self.memory.list_facts("t1", limit=2, offset=2)
        self.assertEqual(len(page1), 2)
        self.assertEqual(len(page2), 2)
        # offset 不应该重复
        ids1 = {f.fact_id for f in page1}
        ids2 = {f.fact_id for f in page2}
        self.assertFalse(ids1 & ids2)

    # ---------- delete_fact_in_tenant ----------

    def test_delete_fact_works(self):
        f = self.memory.add_fact(Fact.make("delete_me", tenant_id="t1"))
        self.assertTrue(self.memory.delete_fact_in_tenant(f.fact_id, "t1"))
        # 再加同 content, 走的是新 fact (hash 反查表已清)
        f2 = self.memory.add_fact(Fact.make("delete_me", tenant_id="t1"))
        self.assertNotEqual(f.fact_id, f2.fact_id)

    def test_delete_unknown_returns_false(self):
        self.assertFalse(self.memory.delete_fact_in_tenant("nonexistent", "t1"))

    # ---------- prune_stale ----------

    def test_prune_stale_drops_unused_old(self):
        # 1. 加 2 个 fact, 把它们 last_accessed 改到很早 + access_count 保持 0
        f1 = self.memory.add_fact(Fact.make("stale_a", tenant_id="t1"))
        f2 = self.memory.add_fact(Fact.make("stale_b", tenant_id="t1"))
        # 直接 patch 时间到 100 天前
        for fid in (f1.fact_id, f2.fact_id):
            loaded = self.memory._load_fact("t1", fid)
            loaded.last_accessed = time.time() - 100 * 86400
            loaded.access_count = 0
            self.memory._save_fact(loaded)
        # 加 1 个新 fact 不该删
        f3 = self.memory.add_fact(Fact.make("fresh", tenant_id="t1"))
        deleted = self.memory.prune_stale("t1", max_age_days=90, min_access_count=1)
        self.assertEqual(deleted, 2)
        remaining = self.memory.list_facts("t1")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].fact_id, f3.fact_id)

    def test_prune_respects_pinned(self):
        f = self.memory.add_fact(Fact.make("pinned_stale", tenant_id="t1", pinned=True))
        loaded = self.memory._load_fact("t1", f.fact_id)
        loaded.last_accessed = time.time() - 200 * 86400
        loaded.access_count = 0
        self.memory._save_fact(loaded)
        deleted = self.memory.prune_stale("t1", max_age_days=90)
        self.assertEqual(deleted, 0)


class TestMemoryInMemoryBackend(_BaseMemoryTests, unittest.TestCase):
    def make_backend(self):
        return InMemoryBackend()


class TestMemorySqliteBackend(_BaseMemoryTests, unittest.TestCase):
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


class TestCrossWorkerMemory(unittest.TestCase):
    """关键: worker A 存 fact → close → worker B 重连同一 SqliteBackend 看见 fact."""

    def test_fact_visible_across_workers(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            backend_a = SqliteBackend(path=path)
            mem_a = LongTermMemoryImpl(backend=backend_a)
            f = mem_a.add_fact(Fact.make("user prefers SQL over NoSQL", tenant_id="t1"))
            backend_a.close()

            backend_b = SqliteBackend(path=path)
            mem_b = LongTermMemoryImpl(backend=backend_b)
            hits = mem_b.search_facts(MemoryQuery(query="SQL", tenant_id="t1"))
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].fact.fact_id, f.fact_id)
            backend_b.close()
        finally:
            try:
                os.unlink(path)
                for suffix in ("-wal", "-shm"):
                    if os.path.exists(path + suffix):
                        os.unlink(path + suffix)
            except Exception:
                pass

    def test_dedup_across_workers(self):
        """worker A 存了 'fact X', worker B 再存 'fact X' → 用户的设计 "存在就标记":
        不重复存, 而是 bump access_count."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            backend_a = SqliteBackend(path=path)
            mem_a = LongTermMemoryImpl(backend=backend_a)
            f1 = mem_a.add_fact(Fact.make("shared fact", tenant_id="t1"))
            backend_a.close()

            backend_b = SqliteBackend(path=path)
            mem_b = LongTermMemoryImpl(backend=backend_b)
            f2 = mem_b.add_fact(Fact.make("shared fact", tenant_id="t1"))
            # 跨 worker 看见同一 fact_id (worker B 的 add 被 dedup 成 bump)
            self.assertEqual(f1.fact_id, f2.fact_id)
            self.assertEqual(f2.access_count, 1)
            backend_b.close()
        finally:
            try:
                os.unlink(path)
                for suffix in ("-wal", "-shm"):
                    if os.path.exists(path + suffix):
                        os.unlink(path + suffix)
            except Exception:
                pass


class TestFactModel(unittest.TestCase):
    """Fact pydantic 模型边界."""

    def test_make_auto_hash(self):
        f = Fact.make("hello", tenant_id="t1")
        self.assertEqual(len(f.content_hash), 16)
        self.assertEqual(len(f.fact_id), 12)

    def test_make_trims(self):
        f = Fact.make("  hello  ", tenant_id="t1")
        self.assertEqual(f.content, "hello")

    def test_confidence_clipped(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            Fact.make("x", confidence=1.5)


if __name__ == "__main__":
    unittest.main()
