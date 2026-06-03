# -*- coding: utf-8 -*-
"""MEM-4: 记忆升级 (方向2 阶段1) 测试 — 可变性分层 / 精炼 / 敏感加密 / 向后兼容。"""
import unittest

from state_backend_module import InMemoryBackend
from long_term_memory_module.core.impl import LongTermMemoryImpl
from long_term_memory_module.model import Fact, MemoryQuery


def _mem(secret_key=None):
    return LongTermMemoryImpl(backend=InMemoryBackend(), secret_key=secret_key)


class TestMemoryLayering(unittest.TestCase):
    def test_mutability_digest_content_type_persisted(self):
        m = _mem()
        s = m.add_fact(Fact.make(content="用户偏好 Python", mutability="refinable",
                                 digest="偏好Python", content_type="preference"))
        loaded = m._load_fact(s.tenant_id, s.fact_id)
        self.assertEqual(loaded.mutability, "refinable")
        self.assertEqual(loaded.digest, "偏好Python")
        self.assertEqual(loaded.content_type, "preference")

    def test_backward_compat_old_fact(self):
        # 旧 Fact JSON 无新字段 → model_validate 用默认值, 不报错
        old = {"fact_id": "abc123", "content": "老事实", "content_hash": "x", "tenant_id": "default"}
        f = Fact.model_validate(old)
        self.assertEqual(f.mutability, "refinable")
        self.assertEqual(f.digest, "")
        self.assertFalse(f.encrypted)


class TestSecretEncryption(unittest.TestCase):
    def test_secret_encrypted_and_revealable(self):
        m = _mem(secret_key="test-secret-key-123")
        s = m.add_fact(Fact.make(content="db_password=hunter2", content_type="secret",
                                 mutability="canonical", digest="生产库密码"))
        self.assertTrue(s.encrypted)
        self.assertNotIn("hunter2", s.content)                       # 明文不落库
        self.assertEqual(m.reveal_fact(s.fact_id, s.tenant_id), "db_password=hunter2")  # 显式解密回明文

    def test_secret_no_key_drops_plaintext(self):
        m = _mem(secret_key="k")
        m._secret_key = None                                          # 强制无密钥
        s = m.add_fact(Fact.make(content="topsecret_value_xyz", content_type="secret"))
        self.assertFalse(s.encrypted)
        self.assertNotIn("topsecret_value_xyz", s.content)           # 无密钥 → 明文被丢弃
        self.assertIn("未存储", s.content)

    def test_secret_dedup_by_content_hash(self):
        m = _mem(secret_key="k123")
        s1 = m.add_fact(Fact.make(content="pwd=AAA", content_type="secret", digest="某密码"))
        s2 = m.add_fact(Fact.make(content="pwd=BBB", content_type="secret", digest="某密码"))
        self.assertNotEqual(s1.fact_id, s2.fact_id)                  # 不同值不去重(即使 digest 相同)
        s3 = m.add_fact(Fact.make(content="pwd=AAA", content_type="secret", digest="某密码"))
        self.assertEqual(s3.fact_id, s1.fact_id)                     # 相同值 → content_hash 精确去重


class TestExtractLayering(unittest.TestCase):
    def test_extract_parses_mutability_digest(self):
        class _LLM:
            def generate(self, p):
                return ('[{"content":"db 在 /var/db", "digest":"数据库路径", '
                        '"mutability":"canonical", "content_type":"path", "confidence":0.9}]')
        m = LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=_LLM())
        facts = m.extract_facts([{"role": "user", "content": "db 在 /var/db"}])
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].mutability, "canonical")
        self.assertEqual(facts[0].digest, "数据库路径")
        self.assertEqual(facts[0].content_type, "path")

    def test_extract_defaults_refinable_on_bad_mutability(self):
        class _LLM:
            def generate(self, p):
                return '[{"content":"某偏好信息内容", "mutability":"weird", "confidence":0.8}]'
        m = LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=_LLM())
        facts = m.extract_facts([{"role": "user", "content": "x"}])
        self.assertEqual(facts[0].mutability, "refinable")           # 非法值兜底 refinable


class TestTwoLevelRetrieval(unittest.TestCase):
    """MEM-5: digest 粗筛 → content 细比 两级检索 (无 embedder, 走子串两级路径)。"""

    def test_digest_plus_content_double_hit(self):
        m = _mem()
        m.add_fact(Fact.make(content="用户偏好 Python 编程", digest="偏好 Python", content_type="preference"))
        hits = m.search_facts(MemoryQuery(query="Python"))
        self.assertTrue(any(h.reason == "digest+content" for h in hits))  # 精炼+原始双命中

    def test_content_only_hit(self):
        m = _mem()
        m.add_fact(Fact.make(content="某文档提到 Django 很流行", digest="文档摘要", content_type="fact"))
        hits = m.search_facts(MemoryQuery(query="Django"))
        self.assertTrue(hits and hits[0].reason == "substring")  # digest 不沾边, 仅原始命中 (兼容旧 reason 名)

    def test_encrypted_secret_findable_via_digest_only(self):
        m = _mem(secret_key="k123")
        m.add_fact(Fact.make(content="db_pwd=hunter2", content_type="secret", digest="生产数据库密码"))
        hits = m.search_facts(MemoryQuery(query="数据库密码"))
        self.assertTrue(len(hits) >= 1)
        self.assertEqual(hits[0].reason, "digest_only")        # content 是密文, 靠 digest 命中
        self.assertNotIn("hunter2", hits[0].fact.content)      # 结果不含明文

    def test_no_match_returns_empty(self):
        m = _mem()
        m.add_fact(Fact.make(content="关于猫的事实", digest="猫"))
        self.assertEqual(m.search_facts(MemoryQuery(query="量子计算")), [])


class TestDifferentialPrune(unittest.TestCase):
    """MEM-6: 差异化清理 — canonical 永久 / refinable 老的删或降级。"""

    def _age(self, m, fact, access=0):
        f = m._load_fact(fact.tenant_id, fact.fact_id)
        f.last_accessed = 0.0
        f.access_count = access
        m._save_fact(f)

    def test_canonical_never_pruned(self):
        m = _mem()
        f = m.add_fact(Fact.make(content="路径 /etc/app", mutability="canonical", digest="配置路径"))
        self._age(m, f)
        m.prune_stale(max_age_days=1, min_access_count=1)
        self.assertIsNotNone(m._load_fact(f.tenant_id, f.fact_id))   # 固定类没被删

    def test_refinable_old_unused_pruned(self):
        m = _mem()
        f = m.add_fact(Fact.make(content="某临时信息内容", mutability="refinable"))  # 无 digest
        self._age(m, f)
        self.assertEqual(m.prune_stale(max_age_days=1, min_access_count=1), 1)  # 老+没用+无精炼 → 删

    def test_degrade_refinable_drops_content_keeps_digest(self):
        m = _mem()
        f = m.add_fact(Fact.make(content="一段很长的原始偏好描述内容XYZ", mutability="refinable", digest="偏好摘要"))
        self._age(m, f, access=3)  # 还在用, 但老
        self.assertEqual(m.degrade_stale_refinable(max_age_days=1), 1)
        d = m._load_fact(f.tenant_id, f.fact_id)
        self.assertNotIn("很长的原始", d.content)   # 原始丢弃
        self.assertEqual(d.digest, "偏好摘要")        # 精炼保留

    def test_degrade_skips_canonical(self):
        m = _mem()
        f = m.add_fact(Fact.make(content="密码值", content_type="secret", mutability="canonical", digest="某密码"))
        self._age(m, f)
        self.assertEqual(m.degrade_stale_refinable(max_age_days=1), 0)  # canonical 不降级


class TestConsolidate(unittest.TestCase):
    """MEM-7: 反思整合 — 多条 refinable 归纳成更高层。"""

    def test_consolidate_adds_higher_level(self):
        class _LLM:
            def generate(self, p):
                return '[{"content":"用户倾向使用 SQLite 而非 Redis", "digest":"偏好SQLite", "tags":["preference"]}]'
        m = LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=_LLM())
        m.add_fact(Fact.make(content="项目1 选了 SQLite", mutability="refinable"))
        m.add_fact(Fact.make(content="项目2 也用 SQLite", mutability="refinable"))
        self.assertGreaterEqual(m.consolidate(), 1)
        hits = m.search_facts(MemoryQuery(query="SQLite"))
        self.assertTrue(any("倾向" in h.fact.content for h in hits))

    def test_consolidate_no_llm_returns_zero(self):
        m = _mem()
        m.add_fact(Fact.make(content="偏好信息一", mutability="refinable"))
        m.add_fact(Fact.make(content="偏好信息二", mutability="refinable"))
        self.assertEqual(m.consolidate(), 0)   # 无 llm_client → 跳过


if __name__ == "__main__":
    unittest.main()
