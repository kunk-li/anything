import os
import shutil
import tempfile
import unittest

import numpy as np

try:
    import faiss  # noqa: F401
    FAISS_AVAILABLE = True
except Exception:
    FAISS_AVAILABLE = False

from vector_db_module.core.impl import FaissVectorDB
from vector_db_module.config.config import VectorDBConfig
from vector_db_module.core.impl import VectorDBException


class _TestVectorDBConfig(VectorDBConfig):
    """测试用配置：绕过 ConfigManager，直接提供参数。"""

    def __init__(self, dim: int, store_dir: str):
        # 不调用父类 __post_init__
        self._dim = dim
        self._dir = store_dir

    def get_vector_dimension(self) -> int:
        return int(self._dim)

    def get_local_store_dir(self) -> str:
        return str(self._dir)

    def get_vector_db_type(self) -> str:
        return "faiss"


@unittest.skipUnless(FAISS_AVAILABLE, "faiss not installed")
class TestFaissVectorDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.tmp)
        self.db = FaissVectorDB(cfg=cfg)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_upsert_and_query(self):
        test_vectors = [
            {
                "vector_id": "v1",
                "embedding": [0.1] * 64,
                "metadata": {"doc_id": "d1", "chunk_id": "d1#c000001"},
            },
            {
                "vector_id": "v2",
                "embedding": [0.2] * 64,
                "metadata": {"doc_id": "d2", "chunk_id": "d2#c000001"},
            },
        ]
        self.assertTrue(self.db.upsert_vectors(test_vectors))

        res = self.db.query([0.1] * 64, top_k=2)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["vector_id"], "v1")
        self.assertIn("doc_id", res[0]["metadata"])

    def test_query_with_filter(self):
        test_vectors = [
            {"vector_id": "v1", "embedding": [0.1] * 64, "metadata": {"doc_id": "d1", "chunk_id": "c1"}},
            {"vector_id": "v2", "embedding": [0.1] * 64, "metadata": {"doc_id": "d1", "chunk_id": "c2"}},
            {"vector_id": "v3", "embedding": [0.1] * 64, "metadata": {"doc_id": "d2", "chunk_id": "c1"}},
        ]
        self.db.upsert_vectors(test_vectors)
        res = self.db.query([0.1] * 64, top_k=10, filter={"doc_id": "d1"})
        self.assertTrue(all(r["metadata"]["doc_id"] == "d1" for r in res))
        self.assertEqual(len(res), 2)

    def test_persist_and_reload(self):
        self.db.upsert_vectors(
            [{"vector_id": "v1", "embedding": [0.3] * 64, "metadata": {"doc_id": "d1", "chunk_id": "c1"}}]
        )
        # 新实例应能加载
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.tmp)
        db2 = FaissVectorDB(cfg=cfg)
        res = db2.query([0.3] * 64, top_k=1)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["vector_id"], "v1")

    def test_delete_not_supported(self):
        with self.assertRaises(VectorDBException) as ctx:
            self.db.delete(vector_ids=["v1"])
        self.assertEqual(ctx.exception.code, "VECTOR_DELETE_NOT_SUPPORTED")

    def test_embedding_dimension_mismatch(self):
        bad = [{"vector_id": "v1", "embedding": [0.1] * 32, "metadata": {"doc_id": "d1", "chunk_id": "c1"}}]
        with self.assertRaises(VectorDBException) as ctx:
            self.db.upsert_vectors(bad)
        self.assertEqual(ctx.exception.code, "VECTOR_INSERT_FAILED")


if __name__ == "__main__":
    unittest.main()
