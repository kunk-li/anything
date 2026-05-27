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
        # 让 v1/v2 的方向真正不同(否则归一化后两个 [0.1]*64 与 [0.2]*64
        # 完全相同,排序不稳定 -> 之前是 pre-existing flaky 测试)
        emb_v1 = [1.0] + [0.0] * 63   # 主要在第 1 维
        emb_v2 = [0.0, 1.0] + [0.0] * 62  # 主要在第 2 维

        test_vectors = [
            {
                "vector_id": "v1",
                "embedding": emb_v1,
                "metadata": {"doc_id": "d1", "chunk_id": "d1#c000001"},
            },
            {
                "vector_id": "v2",
                "embedding": emb_v2,
                "metadata": {"doc_id": "d2", "chunk_id": "d2#c000001"},
            },
        ]
        self.assertTrue(self.db.upsert_vectors(test_vectors))

        # query 与 v1 同方向,v1 必须排第一
        res = self.db.query(emb_v1, top_k=2)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["vector_id"], "v1")
        self.assertIn("doc_id", res[0]["metadata"])
        # 顺便验证 v1 的相似度严格高于 v2
        self.assertGreater(res[0]["score"], res[1]["score"])

    def test_query_with_filter(self):
        test_vectors = [
            {"vector_id": "v1", "embedding": [0.1] * 64, "metadata": {"doc_id": "d1", "chunk_id": "c1"}},
            {"vector_id": "v2", "embedding": [0.1] * 64, "metadata": {"doc_id": "d1", "chunk_id": "c2"}},
            {"vector_id": "v3", "embedding": [0.1] * 64, "metadata": {"doc_id": "d2", "chunk_id": "c1"}},
        ]
        self.db.upsert_vectors(test_vectors)
        res = self.db.query([0.1] * 64, top_k=10, filters={"doc_id": "d1"})
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

    # ============ Task #33 PR3a: tenant_id 接口扩展 ============

    def test_tenant_id_default_when_not_specified(self):
        """不传 tenant_id 应走 default(向后兼容)"""
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.tmp)
        db = FaissVectorDB(cfg=cfg)
        self.assertEqual(db.tenant_id, "default")

    def test_tenant_id_stored_when_specified(self):
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.tmp)
        db = FaissVectorDB(cfg=cfg, tenant_id="tenant-a")
        self.assertEqual(db.tenant_id, "tenant-a")

    def test_tenant_id_invalid_charset_rejected(self):
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.tmp)
        for bad in ("Acme Corp", "../../etc", "ab", "x" * 33, "tenant/a", 123):
            with self.assertRaises(VectorDBException, msg=f"should reject {bad!r}"):
                FaissVectorDB(cfg=cfg, tenant_id=bad)

    def test_tenant_id_does_not_change_behavior_yet(self):
        """PR3a 行为不变: tenant_id 仅记录, query/upsert 走单一物理路径"""
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.tmp)
        db_a = FaissVectorDB(cfg=cfg, tenant_id="tenant-a")
        db_a.upsert_vectors([{
            "vector_id": "v1", "embedding": [1.0] + [0.0] * 63,
            "metadata": {"doc_id": "d1", "chunk_id": "c1"},
        }])
        # 不同 tenant_id 但同 store_dir 在 PR3a 阶段仍能互相看见
        # (PR3b 才会按目录隔离, 届时该测试需要更新)
        db_b = FaissVectorDB(cfg=cfg, tenant_id="tenant-b")
        res = db_b.query([1.0] + [0.0] * 63, top_k=5)
        self.assertEqual(len(res), 1, "PR3a 行为不变: 跨租户仍可见; PR3b 后此断言会反转")


if __name__ == "__main__":
    unittest.main()
