# -*- coding: utf-8 -*-
"""
BM25Retriever + RRF 融合 单元测试 (Task #49)

覆盖:
    - tokenize: ASCII + 中文逐字
    - BM25Retriever: index → query → 排序 + IDF + 长度归一化
    - save/load 双向持久化
    - rrf_merge: 两路结果融合排序
"""

import json
import os
import tempfile
import unittest

from rag_module.extensions import BM25Retriever, rrf_merge, bm25_tokenize


# ===========================================================================
# tokenize
# ===========================================================================

class TestTokenize(unittest.TestCase):
    def test_tokenize_ascii_words(self):
        # ASCII 单词整词出, 大小写归一
        self.assertEqual(bm25_tokenize("Hello World"), ["hello", "world"])

    def test_tokenize_chinese_chars(self):
        # 中文逐字切, 不需要外部分词器
        toks = bm25_tokenize("检索系统")
        self.assertEqual(toks, ["检", "索", "系", "统"])

    def test_tokenize_mixed(self):
        toks = bm25_tokenize("RAG 检索 system 2.0")
        # ASCII 部分整词, 中文逐字, 数字 2/0 因 . 不在字符类被拆
        self.assertIn("rag", toks)
        self.assertIn("system", toks)
        self.assertIn("检", toks)
        self.assertIn("索", toks)
        self.assertIn("2", toks)

    def test_tokenize_empty(self):
        self.assertEqual(bm25_tokenize(""), [])
        self.assertEqual(bm25_tokenize(None), [])

    def test_tokenize_punctuation_stripped(self):
        toks = bm25_tokenize("hello, world! How?")
        self.assertEqual(toks, ["hello", "world", "how"])


# ===========================================================================
# BM25Retriever
# ===========================================================================

class TestBM25Retriever(unittest.TestCase):
    def _make_corpus(self):
        return [
            {"chunk_id": "c1", "doc_id": "d1", "file_name": "a.md",
             "chunk_index": 0, "content": "RAG retrieval augmented generation system"},
            {"chunk_id": "c2", "doc_id": "d1", "file_name": "a.md",
             "chunk_index": 1, "content": "Agent with tool calling and ReAct loop"},
            {"chunk_id": "c3", "doc_id": "d2", "file_name": "b.md",
             "chunk_index": 0, "content": "向量检索 BM25 混合检索 hybrid"},
            {"chunk_id": "c4", "doc_id": "d2", "file_name": "b.md",
             "chunk_index": 1, "content": "FastAPI WebSocket streaming token level"},
        ]

    def test_add_chunks_and_size(self):
        bm25 = BM25Retriever()
        added = bm25.add_chunks(self._make_corpus())
        self.assertEqual(added, 4)
        self.assertEqual(bm25.size, 4)
        self.assertGreater(bm25.avg_doc_len, 0)

    def test_query_keyword_hit(self):
        bm25 = BM25Retriever()
        bm25.add_chunks(self._make_corpus())
        results = bm25.query("WebSocket streaming", top_k=3)
        self.assertTrue(len(results) >= 1)
        # c4 命中两个关键字, 应该是第一
        self.assertEqual(results[0]["chunk_id"], "c4")
        self.assertIn("score", results[0])
        self.assertGreater(results[0]["score"], 0)

    def test_query_chinese(self):
        bm25 = BM25Retriever()
        bm25.add_chunks(self._make_corpus())
        # "混合" 拆成 "混" + "合", 两字都在 c3 出现
        results = bm25.query("混合检索", top_k=3)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["chunk_id"], "c3")

    def test_query_empty_or_no_match(self):
        bm25 = BM25Retriever()
        bm25.add_chunks(self._make_corpus())
        self.assertEqual(bm25.query("", top_k=3), [])
        self.assertEqual(bm25.query("zzzz_not_in_corpus", top_k=3), [])

    def test_query_returns_chunk_shape(self):
        bm25 = BM25Retriever()
        bm25.add_chunks(self._make_corpus())
        results = bm25.query("RAG", top_k=3)
        self.assertTrue(results)
        r = results[0]
        # 必备字段: chunk_id / doc_id / file_name / chunk_index / content / score
        for f in ("chunk_id", "doc_id", "file_name", "chunk_index", "content", "score"):
            self.assertIn(f, r, f"missing field: {f}")

    def test_idf_penalizes_common_terms(self):
        """IDF 应该惩罚高 df 的常见词, 让稀有词加权更高."""
        bm25 = BM25Retriever()
        # 全部 chunk 都含 'common'; 仅 c1 含 'rare'
        bm25.add_chunks([
            {"chunk_id": f"c{i}", "doc_id": "d", "content": "common word here"}
            for i in range(5)
        ])
        bm25.add_chunks([{"chunk_id": "crare", "doc_id": "d", "content": "rare special term"}])
        # query 既含 common 又含 rare, 命中 rare 的 chunk 分数应更高
        r = bm25.query("common rare", top_k=10)
        self.assertEqual(r[0]["chunk_id"], "crare")

    def test_long_doc_length_normalization(self):
        """BM25 长度归一: 同样 tf, 短文档分数应高于长文档."""
        bm25 = BM25Retriever()
        bm25.add_chunks([
            {"chunk_id": "short", "doc_id": "d", "content": "alpha beta"},
            {"chunk_id": "long", "doc_id": "d",
             "content": "alpha " + "filler " * 50 + "beta"},
        ])
        results = bm25.query("alpha", top_k=2)
        # 短文档命中应排第一
        self.assertEqual(results[0]["chunk_id"], "short")

    def test_add_chunks_idempotent_overwrite(self):
        """同 chunk_id 二次 add 应该覆盖, 而不是重复贡献."""
        bm25 = BM25Retriever()
        chunk = {"chunk_id": "c1", "doc_id": "d", "content": "alpha alpha alpha"}
        bm25.add_chunks([chunk])
        s1 = bm25.size
        bm25.add_chunks([chunk])
        self.assertEqual(bm25.size, s1)
        # 替换内容
        bm25.add_chunks([{"chunk_id": "c1", "doc_id": "d", "content": "beta"}])
        r = bm25.query("alpha", top_k=3)
        # alpha 已不在 c1 里
        self.assertEqual(r, [])
        r2 = bm25.query("beta", top_k=3)
        self.assertEqual(r2[0]["chunk_id"], "c1")

    def test_save_load_roundtrip(self):
        bm25 = BM25Retriever()
        bm25.add_chunks(self._make_corpus())
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as f:
            tmp = f.name
        try:
            ok = bm25.save(tmp)
            self.assertTrue(ok)
            # 文件存在 + 是合法 JSON
            with open(tmp, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("inverted", data)
            self.assertIn("doc_lens", data)
            # 新实例 load
            bm25b = BM25Retriever()
            self.assertTrue(bm25b.load(tmp))
            self.assertEqual(bm25b.size, bm25.size)
            r_a = bm25.query("RAG", top_k=2)
            r_b = bm25b.query("RAG", top_k=2)
            self.assertEqual(
                [c["chunk_id"] for c in r_a],
                [c["chunk_id"] for c in r_b],
            )
        finally:
            os.unlink(tmp)

    def test_load_missing_file(self):
        bm25 = BM25Retriever()
        self.assertFalse(bm25.load("/nonexistent/path/missing.json"))
        # 状态保持空 — 不抛错
        self.assertEqual(bm25.size, 0)

    def test_clear(self):
        bm25 = BM25Retriever()
        bm25.add_chunks(self._make_corpus())
        self.assertGreater(bm25.size, 0)
        bm25.clear()
        self.assertEqual(bm25.size, 0)
        self.assertEqual(bm25.query("RAG", top_k=3), [])

    def test_skip_chunk_without_id_or_content(self):
        bm25 = BM25Retriever()
        added = bm25.add_chunks([
            {"chunk_id": "c1", "content": "ok"},
            {"chunk_id": "", "content": "no id"},
            {"chunk_id": "c2", "content": ""},
            "not a dict",  # type: ignore
        ])
        self.assertEqual(added, 1)
        self.assertEqual(bm25.size, 1)


# ===========================================================================
# rrf_merge
# ===========================================================================

class TestBM25RemoveDoc(unittest.TestCase):
    """P4: 按 doc_id 在线摘除 — DELETE /documents 不再留 BM25 残留"""

    def _mk(self):
        r = BM25Retriever()
        r.add_chunks([
            {"chunk_id": "d1#c1", "doc_id": "d1", "content": "python web framework"},
            {"chunk_id": "d1#c2", "doc_id": "d1", "content": "python async server"},
            {"chunk_id": "d2#c1", "doc_id": "d2", "content": "rust memory safety"},
        ])
        return r

    def test_remove_doc_removes_all_chunks(self):
        r = self._mk()
        self.assertEqual(r.remove_doc("d1"), 2)
        self.assertEqual(r.size, 1)
        # d1 内容查不到了, d2 不受影响
        self.assertEqual(r.query("python", top_k=10), [])
        res = r.query("rust", top_k=10)
        self.assertEqual([x["chunk_id"] for x in res], ["d2#c1"])

    def test_remove_doc_idempotent_and_missing(self):
        r = self._mk()
        self.assertEqual(r.remove_doc("d1"), 2)
        self.assertEqual(r.remove_doc("d1"), 0)
        self.assertEqual(r.remove_doc("nonexistent"), 0)
        self.assertEqual(r.remove_doc(""), 0)

    def test_remove_doc_cleans_empty_terms(self):
        """词项的 posting 摘空后整个词条回收, 词表不只增不减"""
        r = self._mk()
        self.assertIn("rust", r._inverted)
        r.remove_doc("d2")
        self.assertNotIn("rust", r._inverted)

    def test_remove_doc_persists_via_save_load(self):
        r = self._mk()
        r.remove_doc("d1")
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "bm25.json")
        self.assertTrue(r.save(path))
        r2 = BM25Retriever()
        self.assertTrue(r2.load(path))
        self.assertEqual(r2.size, 1)
        self.assertEqual(r2.query("python", top_k=10), [])

    def test_atomic_save_no_tmp_leftover(self):
        r = self._mk()
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "bm25.json")
        self.assertTrue(r.save(path))
        self.assertTrue(os.path.exists(path))
        self.assertFalse(os.path.exists(path + ".tmp"))


class TestRRFMerge(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(rrf_merge([]), [])
        self.assertEqual(rrf_merge([[], []]), [])

    def test_single_list_passthrough(self):
        lst = [{"chunk_id": "a"}, {"chunk_id": "b"}, {"chunk_id": "c"}]
        out = rrf_merge([lst], k=60)
        self.assertEqual([x["chunk_id"] for x in out], ["a", "b", "c"])
        # 每个都有 rrf_score
        for x in out:
            self.assertIn("rrf_score", x)

    def test_merge_two_lists_consensus_wins(self):
        # vec 排 a > b > c; bm25 也排 a > b > c → a 应稳第一
        vec = [{"chunk_id": "a", "score": 0.9}, {"chunk_id": "b", "score": 0.7},
               {"chunk_id": "c", "score": 0.5}]
        bm25 = [{"chunk_id": "a", "score": 1.2}, {"chunk_id": "b", "score": 1.0},
                {"chunk_id": "c", "score": 0.4}]
        out = rrf_merge([vec, bm25], k=60)
        self.assertEqual(out[0]["chunk_id"], "a")
        self.assertEqual(out[1]["chunk_id"], "b")
        self.assertEqual(out[2]["chunk_id"], "c")

    def test_merge_unique_per_list(self):
        # vec 只命中 a, bm25 只命中 b → 两者都应进结果, 排在前的应该是排名更靠前的那个
        vec = [{"chunk_id": "a"}]
        bm25 = [{"chunk_id": "b"}, {"chunk_id": "c"}]
        out = rrf_merge([vec, bm25], k=60)
        ids = [x["chunk_id"] for x in out]
        self.assertEqual(set(ids), {"a", "b", "c"})
        # a 和 b 都是各自路的 rank=1 → 同分; c rank=2 应靠后
        self.assertEqual(ids[-1], "c")

    def test_merge_uses_max_score_of_two_paths(self):
        vec = [{"chunk_id": "a", "score": 0.3}]
        bm25 = [{"chunk_id": "a", "score": 1.5}]
        out = rrf_merge([vec, bm25], k=60)
        self.assertEqual(out[0]["chunk_id"], "a")
        # score 字段被替换为两路最大值
        self.assertEqual(out[0]["score"], 1.5)
        # rrf_score 应是两路 1/(60+1) 之和
        self.assertAlmostEqual(out[0]["rrf_score"], 2 / 61, places=6)

    def test_merge_drops_items_without_id_field(self):
        out = rrf_merge([[{"score": 0.5}], [{"chunk_id": "a"}]], k=60)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["chunk_id"], "a")


if __name__ == "__main__":
    unittest.main()
