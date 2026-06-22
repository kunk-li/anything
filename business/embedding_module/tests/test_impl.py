from __future__ import annotations

import unittest
from unittest.mock import patch

from embedding_module.core.impl import LLMEmbedding, RAGException, STEmbedding
from embedding_module.model.data_model import EmbeddingRequest
from embedding_module.utils.tool_functions import normalize_vector


class DummySentenceTransformer:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def get_sentence_embedding_dimension(self) -> int:
        return 4

    def encode(
        self,
        texts,
        batch_size: int = 32,
        normalize_embeddings: bool = False,
        convert_to_numpy: bool = False,
    ):
        if isinstance(texts, str):
            return [1.0, 2.0, 3.0, 4.0]
        return [
            [1.0, 2.0, 3.0, 4.0]
            for _ in texts
        ]


class DummyLLMService:
    # 接收 deps 等 kwargs 以匹配真实 LLMService(deps=...) 调用
    def __init__(self, *args, **kwargs):
        pass

    def get_embeddings(self, texts, model_name):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class TestEmbeddingModule(unittest.TestCase):
    @patch("embedding_module.core.impl.SentenceTransformer", DummySentenceTransformer)
    def test_local_embed_single(self):
        emb = STEmbedding()
        vector = emb.embed_text("RAG系统Embedding模块测试文本")
        self.assertEqual(len(vector), 4)

    @patch("embedding_module.core.impl.SentenceTransformer", DummySentenceTransformer)
    def test_local_embed_batch(self):
        emb = STEmbedding()
        vectors = emb.embed_texts(["测试文本1", "测试文本2", "测试文本3"])
        self.assertEqual(len(vectors), 3)
        self.assertEqual(len(vectors[0]), 4)

    @patch("embedding_module.core.impl.LLMService", DummyLLMService)
    def test_remote_embed(self):
        emb = LLMEmbedding()
        vector = emb.embed_text("测试文本")
        self.assertEqual(len(vector), 4)

    @patch("embedding_module.core.impl.SentenceTransformer", DummySentenceTransformer)
    def test_empty_text_embed(self):
        emb = STEmbedding()
        with self.assertRaises(RAGException):
            emb.embed_text("")

    @patch("embedding_module.core.impl.SentenceTransformer", DummySentenceTransformer)
    def test_call_embedding_single(self):
        emb = STEmbedding()
        req = EmbeddingRequest(input_type="SINGLE", single_text="hello")
        resp = emb.call_embedding(req)
        self.assertEqual(resp.code, "SUCCESS")
        self.assertEqual(len(resp.vector_result), 1)

    @patch("embedding_module.core.impl.SentenceTransformer", DummySentenceTransformer)
    def test_call_embedding_batch_size_override_is_not_persisted(self):
        # per-request batch_size 覆盖只能影响本次 encode, 不得回写到共享的
        # self.default_batch_size (长生命周期单例 + 并发下会状态污染/竞态)。
        recorded = []

        class RecordingST(DummySentenceTransformer):
            def encode(self, texts, batch_size: int = 32, **kwargs):
                recorded.append(batch_size)
                return super().encode(texts, batch_size=batch_size, **kwargs)

        with patch("embedding_module.core.impl.SentenceTransformer", RecordingST):
            emb = STEmbedding()
            original_default = emb.default_batch_size

            req = EmbeddingRequest(
                input_type="BATCH",
                batch_texts=["a", "b"],
                batch_size=original_default + 7,
            )
            resp = emb.call_embedding(req)
            self.assertEqual(resp.code, "SUCCESS")
            # 本次 encode 收到的是 per-request 覆盖值
            self.assertIn(original_default + 7, recorded)
            # 共享默认值未被污染
            self.assertEqual(emb.default_batch_size, original_default)

            # 后续不带覆盖的请求仍用原始默认值, 证明无残留状态
            recorded.clear()
            emb.call_embedding(EmbeddingRequest(input_type="BATCH", batch_texts=["c"]))
            self.assertIn(original_default, recorded)

    def test_normalize_vector(self):
        vector = normalize_vector([3.0, 4.0])
        self.assertAlmostEqual(vector[0], 0.6, places=6)
        self.assertAlmostEqual(vector[1], 0.8, places=6)


if __name__ == "__main__":
    unittest.main()