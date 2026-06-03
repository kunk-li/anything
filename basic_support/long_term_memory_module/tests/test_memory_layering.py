# -*- coding: utf-8 -*-
"""MEM-4: 记忆升级 (方向2 阶段1) 测试 — 可变性分层 / 精炼 / 敏感加密 / 向后兼容。"""
import unittest

from state_backend_module import InMemoryBackend
from long_term_memory_module.core.impl import LongTermMemoryImpl
from long_term_memory_module.model import Fact


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


if __name__ == "__main__":
    unittest.main()
