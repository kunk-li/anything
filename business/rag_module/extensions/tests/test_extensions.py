# -*- coding: utf-8 -*-
"""
rag_module.extensions 单元测试

覆盖:
    - LLMQueryRewriter: should_rewrite 触发条件 + JSON 解析 + 降级
    - LLMReranker: rerank 排序 + 漏分保留原 score + 降级
"""

import math
import unittest

from rag_module.extensions import (
    LLMQueryRewriter,
    LLMReranker,
    CrossEncoderReranker,
    RewriteResult,
)


# ===========================================================================
# QueryRewriter
# ===========================================================================


class TestLLMQueryRewriter(unittest.TestCase):

    def _rewriter(self, llm_response: str = ""):
        return LLMQueryRewriter(llm_call=lambda p: llm_response)

    def test_should_rewrite_short_query(self):
        r = self._rewriter()
        self.assertTrue(r.should_rewrite("RAG"))   # < 6 char threshold

    def test_should_not_rewrite_empty_query(self):
        """空 query 不触发改写(没有可改写的内容,直接走主链路)"""
        r = self._rewriter()
        self.assertFalse(r.should_rewrite(""))

    def test_should_rewrite_long_query(self):
        r = self._rewriter()
        self.assertTrue(r.should_rewrite("x" * 300))  # > 256

    def test_should_rewrite_pronoun(self):
        r = self._rewriter()
        self.assertTrue(r.should_rewrite("它的实现方式是什么"))
        self.assertTrue(r.should_rewrite("上面那个怎么用"))

    def test_should_not_rewrite_normal_query(self):
        r = self._rewriter()
        self.assertFalse(r.should_rewrite("RAG 检索增强生成是什么原理"))

    def test_rewrite_valid_response(self):
        r = self._rewriter(
            '{"rewrite_query": "RAG 检索增强生成原理详解", '
            '"keywords": ["RAG", "检索增强生成"], "filters": {"source": "doc"}}'
        )
        result = r.rewrite("RAG")
        self.assertEqual(result.rewrite_query, "RAG 检索增强生成原理详解")
        self.assertEqual(result.keywords, ["RAG", "检索增强生成"])
        self.assertEqual(result.filters, {"source": "doc"})

    def test_rewrite_invalid_json_falls_back(self):
        r = self._rewriter("这不是 JSON")
        result = r.rewrite("它怎么用")
        # 退化为原 query
        self.assertEqual(result.rewrite_query, "它怎么用")

    def test_rewrite_llm_exception_falls_back(self):
        def raise_llm(p):
            raise RuntimeError("LLM down")

        r = LLMQueryRewriter(llm_call=raise_llm)
        result = r.rewrite("它怎么用")
        self.assertEqual(result.rewrite_query, "它怎么用")

    def test_rewrite_markdown_wrapped(self):
        r = self._rewriter(
            "```json\n"
            '{"rewrite_query": "改写后", "keywords": [], "filters": {}}\n'
            "```"
        )
        result = r.rewrite("xx")
        self.assertEqual(result.rewrite_query, "改写后")

    def test_rewrite_result_is_effective(self):
        self.assertTrue(RewriteResult(rewrite_query="新").is_effective("旧"))
        self.assertFalse(RewriteResult(rewrite_query="同").is_effective("同"))
        self.assertFalse(RewriteResult(rewrite_query=" 同 ").is_effective("同"))  # strip 后相同


# ===========================================================================
# Reranker
# ===========================================================================


class TestLLMReranker(unittest.TestCase):

    def _candidates(self):
        return [
            {"chunk_id": "c1", "content": "RAG 是检索增强生成", "score": 0.5},
            {"chunk_id": "c2", "content": "无关内容,关于天气", "score": 0.5},
            {"chunk_id": "c3", "content": "RAG 的核心是 retrieve + generate", "score": 0.5},
        ]

    def test_rerank_with_valid_scores(self):
        # LLM 给 c3 最高分 c1 次之 c2 最低
        llm_response = '[{"chunk_id":"c1","score":0.7},{"chunk_id":"c2","score":0.1},{"chunk_id":"c3","score":0.95}]'
        r = LLMReranker(llm_call=lambda p: llm_response)
        result = r.rerank("RAG 是什么", self._candidates(), top_k=3)

        # 排序应为 c3 > c1 > c2
        self.assertEqual([c["chunk_id"] for c in result], ["c3", "c1", "c2"])
        self.assertEqual(result[0]["score"], 0.95)
        self.assertEqual(result[0]["rerank_source"], "llm")

    def test_rerank_top_k_truncates(self):
        llm_response = '[{"chunk_id":"c1","score":0.7},{"chunk_id":"c2","score":0.1},{"chunk_id":"c3","score":0.95}]'
        r = LLMReranker(llm_call=lambda p: llm_response)
        result = r.rerank("q", self._candidates(), top_k=2)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["chunk_id"], "c3")

    def test_rerank_llm_invalid_json_falls_back(self):
        r = LLMReranker(llm_call=lambda p: "不是 JSON")
        result = r.rerank("q", self._candidates(), top_k=3)
        # 退化为原顺序截断
        self.assertEqual([c["chunk_id"] for c in result], ["c1", "c2", "c3"])

    def test_rerank_llm_exception_falls_back(self):
        def raise_llm(p):
            raise RuntimeError("LLM down")

        r = LLMReranker(llm_call=raise_llm)
        result = r.rerank("q", self._candidates(), top_k=3)
        self.assertEqual([c["chunk_id"] for c in result], ["c1", "c2", "c3"])

    def test_rerank_empty_candidates(self):
        r = LLMReranker(llm_call=lambda p: "[]")
        self.assertEqual(r.rerank("q", [], top_k=3), [])

    def test_rerank_score_clamped(self):
        """LLM 返回超界分数应被夹到 [0, 1]"""
        llm_response = '[{"chunk_id":"c1","score":1.5},{"chunk_id":"c2","score":-0.3},{"chunk_id":"c3","score":0.8}]'
        r = LLMReranker(llm_call=lambda p: llm_response)
        result = r.rerank("q", self._candidates(), top_k=3)
        scores = {c["chunk_id"]: c["score"] for c in result}
        self.assertEqual(scores["c1"], 1.0)
        self.assertEqual(scores["c2"], 0.0)
        self.assertEqual(scores["c3"], 0.8)

    def test_rerank_missing_score_keeps_original(self):
        """LLM 漏打分的 chunk 应保留原 score, 不丢失候选"""
        llm_response = '[{"chunk_id":"c1","score":0.9}]'  # 只给了 c1
        r = LLMReranker(llm_call=lambda p: llm_response)
        result = r.rerank("q", self._candidates(), top_k=3)
        # 所有 3 个候选都该返回 (c1 排第一,因为 0.9 > 0.5)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["chunk_id"], "c1")
        self.assertNotIn("rerank_source", result[1])  # 没被 LLM rerank 的没有 rerank_source 字段


class _MockCrossEncoderModel:
    """模拟 sentence_transformers CrossEncoder 接口的最小实现 (不下载真实模型)。

    predict(pairs) 返回每对的 logits (raw, 可负数). 测试中用预定义脚本.
    """

    def __init__(self, scripted_logits=None):
        self.scripted_logits = scripted_logits or []
        self.last_pairs = None

    def predict(self, pairs):
        self.last_pairs = pairs
        if self.scripted_logits:
            return self.scripted_logits[: len(pairs)]
        # 默认按 candidate 顺序递减打分
        return [float(len(pairs) - i) for i in range(len(pairs))]


class TestCrossEncoderReranker(unittest.TestCase):

    def _candidates(self):
        return [
            {"chunk_id": "c1", "content": "RAG 是检索增强生成", "score": 0.5},
            {"chunk_id": "c2", "content": "无关内容,关于天气", "score": 0.5},
            {"chunk_id": "c3", "content": "RAG 的核心是 retrieve + generate", "score": 0.5},
        ]

    def test_rerank_with_mock_scores(self):
        # 给 c1 logits=2.0 (sigmoid ~0.88), c2=-2.0 (~0.12), c3=3.0 (~0.95)
        mock_model = _MockCrossEncoderModel(scripted_logits=[2.0, -2.0, 3.0])
        r = CrossEncoderReranker(model=mock_model)
        result = r.rerank("RAG 是什么", self._candidates(), top_k=3)

        # 排序应为 c3 > c1 > c2 (按 sigmoid 归一化后 0.95 > 0.88 > 0.12)
        self.assertEqual([c["chunk_id"] for c in result], ["c3", "c1", "c2"])
        # 全部应有 rerank_source 标记
        for c in result:
            self.assertEqual(c["rerank_source"], "cross_encoder")
        # 分数应在 [0, 1] 区间
        for c in result:
            self.assertGreaterEqual(c["score"], 0.0)
            self.assertLessEqual(c["score"], 1.0)
        # sigmoid(3.0) ≈ 0.953
        self.assertAlmostEqual(result[0]["score"], 1.0 / (1.0 + math.exp(-3.0)), places=5)

    def test_rerank_top_k_truncates(self):
        mock_model = _MockCrossEncoderModel(scripted_logits=[1.0, 2.0, 3.0])
        r = CrossEncoderReranker(model=mock_model)
        result = r.rerank("q", self._candidates(), top_k=2)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["chunk_id"], "c3")  # 最高分

    def test_rerank_empty_candidates(self):
        mock_model = _MockCrossEncoderModel()
        r = CrossEncoderReranker(model=mock_model)
        self.assertEqual(r.rerank("q", [], top_k=3), [])

    def test_rerank_empty_query_falls_back(self):
        """空 query -> 退化为原顺序截断"""
        mock_model = _MockCrossEncoderModel()
        r = CrossEncoderReranker(model=mock_model)
        result = r.rerank("", self._candidates(), top_k=2)
        # 应该按原顺序截断, 而非调 model
        self.assertEqual([c["chunk_id"] for c in result], ["c1", "c2"])

    def test_rerank_model_load_failure_falls_back(self):
        """模型 None (load 失败) -> 退化为原顺序"""
        r = CrossEncoderReranker(model=None)
        # 不传 _model, 也不会触发真实加载(因为我们用 monkeypatch _get_model)
        r._get_model = lambda: None  # 模拟加载失败
        result = r.rerank("q", self._candidates(), top_k=3)
        self.assertEqual([c["chunk_id"] for c in result], ["c1", "c2", "c3"])

    def test_rerank_predict_exception_falls_back(self):
        class _BrokenModel:
            def predict(self, pairs):
                raise RuntimeError("model crashed")
        r = CrossEncoderReranker(model=_BrokenModel())
        result = r.rerank("q", self._candidates(), top_k=3)
        self.assertEqual([c["chunk_id"] for c in result], ["c1", "c2", "c3"])

    def test_content_truncate_applied(self):
        """超长 content 应被截断到 content_truncate_chars"""
        mock_model = _MockCrossEncoderModel()
        r = CrossEncoderReranker(model=mock_model, content_truncate_chars=10)
        long_content = "x" * 100
        candidates = [{"chunk_id": "c1", "content": long_content, "score": 0.5}]
        r.rerank("q", candidates, top_k=1)
        # 检查实际传给 predict 的 pair 中 content 已被截断
        pair = mock_model.last_pairs[0]
        self.assertEqual(len(pair[1]), 10)


if __name__ == "__main__":
    unittest.main()
