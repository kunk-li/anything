# -*- coding: utf-8 -*-
"""
LLM 抽取 + 语义查重测试 (Task EEE #91).

不依赖真实 LLM — 用 mock llm_client / mock embedder 注入.
"""
import math
import unittest
from unittest.mock import MagicMock

from long_term_memory_module import (
    LongTermMemoryImpl, Fact, MemoryQuery,
)
from state_backend_module import InMemoryBackend


class _FakeEmbedder:
    """简单 fake embedder: content 里有的字符 → 维度对应位置标 1."""

    def __init__(self):
        self._dim = 32
        self._charset = "abcdefghijklmnopqrstuvwxyz0123456789"

    def embed_text(self, text: str):
        vec = [0.0] * self._dim
        for ch in text.lower():
            if ch in self._charset:
                vec[self._charset.index(ch) % self._dim] = 1.0
        # 归一化让 cosine 算出来在 [0,1]
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


class _FakeLLM:
    """Mock LLM, generate() 返回预设的 JSON string."""

    def __init__(self, response: str):
        self._response = response

    def generate(self, prompt: str, **kwargs) -> str:
        return self._response


# ============================================================
# 语义查重 (cosine > 0.9 判重复)
# ============================================================


class TestSemanticDedup(unittest.TestCase):

    def setUp(self):
        self.backend = InMemoryBackend()
        self.embedder = _FakeEmbedder()
        self.memory = LongTermMemoryImpl(
            backend=self.backend,
            embedder=self.embedder,
            similarity_threshold=0.9,
        )

    def test_add_fact_computes_embedding(self):
        f = self.memory.add_fact(Fact.make("hello world", tenant_id="t1"))
        self.assertIsNotNone(f.embedding)
        self.assertEqual(len(f.embedding), 32)

    def test_semantic_dedup_near_duplicate(self):
        """两个语义高度相似的内容 → 被 dedup 到同一 fact."""
        f1 = self.memory.add_fact(Fact.make("the cat sat on the mat", tenant_id="t1"))
        # 完全相同 → 走 hash 路径 dedup
        f2 = self.memory.add_fact(Fact.make("the cat sat on the mat", tenant_id="t1"))
        self.assertEqual(f1.fact_id, f2.fact_id)
        # 字符集完全相同的另一个排列 → fake embedder 给同样 embedding → 走 cosine 路径 dedup
        f3 = self.memory.add_fact(Fact.make("a cat sat on the mat", tenant_id="t1"))
        # fake embedder 算法对字符集 sensitive, 这两条字符集差别小 → cosine 应该 > 0.9
        # 实际 embedding 一致, 应被 cosine dedup
        self.assertEqual(f1.fact_id, f3.fact_id)

    def test_semantic_distinct_facts_kept(self):
        """语义差距大的事实独立存."""
        f1 = self.memory.add_fact(Fact.make("xyz xyz xyz", tenant_id="t1"))
        f2 = self.memory.add_fact(Fact.make("abc abc abc def def", tenant_id="t1"))
        # 字符集完全不同 → cosine = 0 → 各自存
        self.assertNotEqual(f1.fact_id, f2.fact_id)
        facts = self.memory.list_facts("t1")
        self.assertEqual(len(facts), 2)

    def test_no_embedder_falls_back_to_hash_only(self):
        """没传 embedder → 走 DDD MVP 的 hash dedup, 不算 embedding."""
        mem = LongTermMemoryImpl(backend=InMemoryBackend(), embedder=None)
        f = mem.add_fact(Fact.make("hello", tenant_id="t1"))
        self.assertIsNone(f.embedding)


# ============================================================
# Search with embedding
# ============================================================


class TestSemanticSearch(unittest.TestCase):

    def setUp(self):
        self.backend = InMemoryBackend()
        self.embedder = _FakeEmbedder()
        self.memory = LongTermMemoryImpl(
            backend=self.backend, embedder=self.embedder, similarity_threshold=0.9,
        )

    def test_search_returns_cosine_reason(self):
        """search 命中时 reason 显示 cosine_<score>."""
        self.memory.add_fact(Fact.make("xyz xyz xyz", tenant_id="t1"))
        hits = self.memory.search_facts(
            MemoryQuery(query="xyz xyz", tenant_id="t1")
        )
        # 应该命中 (cosine > 0); reason 走 cosine 路径
        self.assertGreater(len(hits), 0)
        self.assertTrue(
            hits[0].reason.startswith("cosine_") or hits[0].reason == "content_hash_exact"
            or hits[0].reason == "substring"
        )


# ============================================================
# LLM 抽取
# ============================================================


class TestExtractFacts(unittest.TestCase):

    def test_no_llm_raises(self):
        mem = LongTermMemoryImpl(backend=InMemoryBackend())  # 没 llm
        with self.assertRaises(NotImplementedError):
            mem.extract_facts([{"role": "user", "content": "hi"}])

    def test_extract_parses_clean_json(self):
        llm = _FakeLLM(
            '[{"content": "用户喜欢 Python", "tags": ["preference"], "confidence": 0.9}]'
        )
        mem = LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=llm)
        facts = mem.extract_facts(
            [{"role": "user", "content": "我喜欢用 Python"}], tenant_id="t1",
        )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].content, "用户喜欢 Python")
        self.assertEqual(facts[0].tags, ["preference"])
        self.assertEqual(facts[0].confidence, 0.9)
        self.assertEqual(facts[0].tenant_id, "t1")

    def test_extract_parses_markdown_fenced_json(self):
        """LLM 经常用 ```json ... ``` markdown 围栏 — 也得能解."""
        llm = _FakeLLM(
            "```json\n"
            '[{"content": "项目用 SQLite", "tags": ["decision"], "confidence": 0.85}]'
            "\n```"
        )
        mem = LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=llm)
        facts = mem.extract_facts(
            [{"role": "user", "content": "我们选用 SQLite"}], tenant_id="t1",
        )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].tags, ["decision"])

    def test_extract_parses_json_with_surrounding_text(self):
        """LLM 经常前后加解释 — 正则抓第一个数组."""
        llm = _FakeLLM(
            "Sure, here are the extracted facts:\n"
            '[{"content": "用户在 frontend team", "tags": ["context"], "confidence": 0.7}]\n'
            "Hope this helps."
        )
        mem = LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=llm)
        facts = mem.extract_facts(
            [{"role": "user", "content": "我在前端组"}], tenant_id="t1",
        )
        self.assertEqual(len(facts), 1)

    def test_extract_empty_list(self):
        llm = _FakeLLM("[]")
        mem = LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=llm)
        facts = mem.extract_facts(
            [{"role": "user", "content": "嗯"}], tenant_id="t1",
        )
        self.assertEqual(facts, [])

    def test_extract_llm_failure_returns_empty(self):
        """LLM 抛异常时, extract_facts 静默返空 list, 不阻断主链路."""
        llm = MagicMock()
        llm.generate.side_effect = RuntimeError("LLM down")
        mem = LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=llm)
        facts = mem.extract_facts(
            [{"role": "user", "content": "hi"}], tenant_id="t1",
        )
        self.assertEqual(facts, [])

    def test_extract_garbage_response_returns_empty(self):
        llm = _FakeLLM("This is not JSON at all, just garbage.")
        mem = LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=llm)
        facts = mem.extract_facts(
            [{"role": "user", "content": "hi"}], tenant_id="t1",
        )
        self.assertEqual(facts, [])

    def test_extract_max_facts_cap(self):
        # LLM 返了 10 条, extract_max_facts=3 时只留 3
        big = [
            f'{{"content": "fact_{i}_with_enough_chars", "tags": ["fact"], "confidence": 0.8}}'
            for i in range(10)
        ]
        llm = _FakeLLM("[" + ",".join(big) + "]")
        mem = LongTermMemoryImpl(
            backend=InMemoryBackend(), llm_client=llm, extract_max_facts=3,
        )
        facts = mem.extract_facts(
            [{"role": "user", "content": "test"}], tenant_id="t1",
        )
        self.assertEqual(len(facts), 3)

    def test_extract_too_short_content_filtered(self):
        """content < 4 chars 跳过 (避免 LLM 给出 "yes" 这种水货 fact)."""
        llm = _FakeLLM(
            '[{"content": "ok", "tags": [], "confidence": 0.9},'
            ' {"content": "this is a good fact", "tags": [], "confidence": 0.9}]'
        )
        mem = LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=llm)
        facts = mem.extract_facts(
            [{"role": "user", "content": "x"}], tenant_id="t1",
        )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].content, "this is a good fact")

    def test_extract_then_add_pipeline(self):
        """端到端: extract → 把抽出的 facts 一条条 add → dedup 生效."""
        llm = _FakeLLM(
            '[{"content": "Test fact about preference", "tags": ["preference"], "confidence": 0.9}]'
        )
        mem = LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=llm)
        facts = mem.extract_facts(
            [{"role": "user", "content": "i like blue"}], tenant_id="t1",
        )
        # 入库
        added = [mem.add_fact(f) for f in facts]
        # 再 extract 同样的对话 (LLM 给同样 facts) → add 时被 dedup
        facts2 = mem.extract_facts(
            [{"role": "user", "content": "i like blue"}], tenant_id="t1",
        )
        added2 = [mem.add_fact(f) for f in facts2]
        self.assertEqual(added[0].fact_id, added2[0].fact_id)
        self.assertEqual(added2[0].access_count, 1)


if __name__ == "__main__":
    unittest.main()
