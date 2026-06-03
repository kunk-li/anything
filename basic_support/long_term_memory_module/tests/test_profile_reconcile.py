# -*- coding: utf-8 -*-
"""方向1 阶段3: 画像冲突消解 + 时效。

验证:
    1. reconcile_conflicts: LLM 标出对立组 → 组内最新(created_at)胜, 旧的标 superseded_by
    2. superseded fact 不进 get_user_profile / 不被 search_facts 返回
    3. 时效: get_user_profile 按 created_at 让新偏好领先(即便旧的 access_count 高)
    4. 无 llm_client / 无对立 / LLM 返回拍平数组 → 安全返 0
    5. 向后兼容: 旧 Fact JSON 无 superseded_by → 默认 None
"""
import unittest

from state_backend_module import InMemoryBackend
from long_term_memory_module.core.impl import LongTermMemoryImpl
from long_term_memory_module.model import Fact, MemoryQuery


def _mem(llm_client=None):
    return LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=llm_client)


class _ConflictLLM:
    """mock: 把编号 0、1 判为对立组。"""
    def generate(self, p):
        return "[[0, 1]]"


class TestReconcileConflicts(unittest.TestCase):
    def test_supersedes_older_conflicting(self):
        m = _mem(llm_client=_ConflictLLM())
        old = m.add_fact(Fact.make(content="偏好详细冗长的解释", mutability="refinable",
                                   content_type="preference", created_at=100.0))
        new = m.add_fact(Fact.make(content="偏好简洁直接的回答", mutability="refinable",
                                   content_type="preference", created_at=200.0))
        n = m.reconcile_conflicts()
        self.assertEqual(n, 1)
        old_loaded = m._load_fact(old.tenant_id, old.fact_id)
        new_loaded = m._load_fact(new.tenant_id, new.fact_id)
        # 旧的(created_at 小)被标 superseded, 指向新的; 新的保持有效
        self.assertEqual(old_loaded.superseded_by, new.fact_id)
        self.assertIsNone(new_loaded.superseded_by)

    def test_reconciled_old_pref_leaves_profile(self):
        m = _mem(llm_client=_ConflictLLM())
        m.add_fact(Fact.make(content="偏好详细冗长的解释", mutability="refinable",
                             content_type="preference", digest="要详细", created_at=100.0))
        m.add_fact(Fact.make(content="偏好简洁直接的回答", mutability="refinable",
                             content_type="preference", digest="要简洁", created_at=200.0))
        m.reconcile_conflicts()
        p = m.get_user_profile()
        self.assertIn("要简洁", p.get("preference", []))
        self.assertNotIn("要详细", p.get("preference", []))

    def test_no_llm_returns_zero(self):
        m = _mem()
        m.add_fact(Fact.make(content="偏好一", mutability="refinable", content_type="preference"))
        m.add_fact(Fact.make(content="偏好二", mutability="refinable", content_type="preference"))
        self.assertEqual(m.reconcile_conflicts(), 0)

    def test_no_conflict_returns_zero(self):
        class _LLM:
            def generate(self, p):
                return "[]"
        m = _mem(llm_client=_LLM())
        m.add_fact(Fact.make(content="偏好一", mutability="refinable", content_type="preference"))
        m.add_fact(Fact.make(content="偏好二", mutability="refinable", content_type="preference"))
        self.assertEqual(m.reconcile_conflicts(), 0)

    def test_flat_array_fail_safe(self):
        # LLM 把数组拍平成 [0,1] 而非 [[0,1]] → 单个编号不构成组, 安全不消解
        class _LLM:
            def generate(self, p):
                return "[0, 1]"
        m = _mem(llm_client=_LLM())
        m.add_fact(Fact.make(content="偏好一", mutability="refinable", content_type="preference"))
        m.add_fact(Fact.make(content="偏好二", mutability="refinable", content_type="preference"))
        self.assertEqual(m.reconcile_conflicts(), 0)

    def test_fewer_than_two_returns_zero(self):
        m = _mem(llm_client=_ConflictLLM())
        m.add_fact(Fact.make(content="只有一条", mutability="refinable", content_type="preference"))
        self.assertEqual(m.reconcile_conflicts(), 0)


class TestSupersededExcluded(unittest.TestCase):
    def test_superseded_not_in_profile(self):
        m = _mem()
        a = m.add_fact(Fact.make(content="旧偏好X", content_type="preference", digest="旧X"))
        b = m.add_fact(Fact.make(content="新偏好Y", content_type="preference", digest="新Y"))
        a.superseded_by = b.fact_id
        m._save_fact(a)
        p = m.get_user_profile()
        self.assertNotIn("旧X", p.get("preference", []))
        self.assertIn("新Y", p.get("preference", []))

    def test_superseded_not_in_search(self):
        m = _mem()
        a = m.add_fact(Fact.make(content="旧偏好X", content_type="preference", digest="某偏好"))
        b = m.add_fact(Fact.make(content="新偏好Y", content_type="preference", digest="某偏好"))
        a.superseded_by = b.fact_id
        m._save_fact(a)
        # 即便精确命中 a 的 content, superseded 也不返回
        hits = m.search_facts(MemoryQuery(query="旧偏好X"))
        self.assertFalse(any(h.fact.fact_id == a.fact_id for h in hits))


class TestTimeliness(unittest.TestCase):
    def test_profile_prefers_recent_over_high_access(self):
        m = _mem()
        # 老偏好 access_count 很高, 新偏好刚学到 access_count=0
        m.add_fact(Fact.make(content="老偏好", content_type="preference", digest="老",
                             created_at=100.0, access_count=99))
        m.add_fact(Fact.make(content="新偏好", content_type="preference", digest="新",
                             created_at=200.0, access_count=0))
        p = m.get_user_profile(per_dim=1)
        # created_at 主导: 新的领先, 即便老的 access_count 高
        self.assertEqual(p["preference"], ["新"])


class TestBackwardCompat(unittest.TestCase):
    def test_old_fact_defaults_superseded_none(self):
        old = {"fact_id": "abc123", "content": "老事实", "content_hash": "x", "tenant_id": "default"}
        f = Fact.model_validate(old)
        self.assertIsNone(f.superseded_by)


if __name__ == "__main__":
    unittest.main()
