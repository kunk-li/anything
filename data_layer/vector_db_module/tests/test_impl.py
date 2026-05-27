import os

# 单独跑本测试时启用 dev mode 避免 build_basic_deps 触发 secrets fail-fast
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

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

    def test_tenant_isolation_cross_tenant_zero_hit(self):
        """PR3b 行为反转: tenant A 写, tenant B 读应 0 命中 (目录分区)"""
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.tmp)
        db_a = FaissVectorDB(cfg=cfg, tenant_id="tenant-a")
        db_a.upsert_vectors([{
            "vector_id": "v1", "embedding": [1.0] + [0.0] * 63,
            "metadata": {"doc_id": "d1", "chunk_id": "c1"},
        }])
        # 验证物理目录隔离
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "tenant-a", "faiss.index")))

        db_b = FaissVectorDB(cfg=cfg, tenant_id="tenant-b")
        res = db_b.query([1.0] + [0.0] * 63, top_k=5)
        self.assertEqual(len(res), 0, "PR3b: 跨租户必须 0 命中(目录已隔离)")

    def test_tenant_isolation_same_tenant_can_see(self):
        """同 tenant 跨实例仍可见(持久化 + 加载 OK)"""
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.tmp)
        db1 = FaissVectorDB(cfg=cfg, tenant_id="tenant-a")
        db1.upsert_vectors([{
            "vector_id": "v1", "embedding": [1.0] + [0.0] * 63,
            "metadata": {"doc_id": "d1", "chunk_id": "c1"},
        }])
        # 新实例加载同 tenant 数据
        db2 = FaissVectorDB(cfg=cfg, tenant_id="tenant-a")
        res = db2.query([1.0] + [0.0] * 63, top_k=5)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["vector_id"], "v1")

    def test_default_tenant_fallback_reads_legacy_flat(self):
        """default 租户当子目录无数据时, fallback 读老扁平路径"""
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.tmp)
        # 直接在 self.tmp (扁平路径) 写一份老数据
        # 用一个临时实例伪装 default 写, 但要让数据先落在扁平路径
        # 简单做法: 先建实例写入 tenant=default 后, 把数据搬到扁平路径
        db_seed = FaissVectorDB(cfg=cfg, tenant_id="default")
        db_seed.upsert_vectors([{
            "vector_id": "legacy_v1", "embedding": [1.0] + [0.0] * 63,
            "metadata": {"doc_id": "d_legacy", "chunk_id": "c_legacy"},
        }])
        # 模拟"老数据在扁平路径": 把 default 子目录文件搬到 self.tmp
        import shutil
        for fname in ("faiss.index", "meta.json", "embeddings.npy"):
            src = os.path.join(self.tmp, "default", fname)
            if os.path.exists(src):
                shutil.move(src, os.path.join(self.tmp, fname))
        shutil.rmtree(os.path.join(self.tmp, "default"), ignore_errors=True)

        # 新建 default 租户实例: 子目录已空, 应 fallback 读老扁平
        db_new = FaissVectorDB(cfg=cfg, tenant_id="default")
        res = db_new.query([1.0] + [0.0] * 63, top_k=5)
        self.assertEqual(len(res), 1, "default 租户应 fallback 读到老扁平数据")
        self.assertEqual(res[0]["vector_id"], "legacy_v1")

    def test_non_default_tenant_no_fallback(self):
        """非 default 租户禁止 fallback 老扁平路径(防越权)"""
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.tmp)
        # 在扁平路径放假数据 (模拟其他租户的老数据混在里面)
        import shutil
        db_seed = FaissVectorDB(cfg=cfg, tenant_id="default")
        db_seed.upsert_vectors([{
            "vector_id": "should_not_be_seen", "embedding": [1.0] + [0.0] * 63,
            "metadata": {"doc_id": "d", "chunk_id": "c"},
        }])
        for fname in ("faiss.index", "meta.json", "embeddings.npy"):
            src = os.path.join(self.tmp, "default", fname)
            if os.path.exists(src):
                shutil.move(src, os.path.join(self.tmp, fname))
        shutil.rmtree(os.path.join(self.tmp, "default"), ignore_errors=True)

        # tenant-x 不应该看到扁平路径的数据
        db_x = FaissVectorDB(cfg=cfg, tenant_id="tenant-x")
        res = db_x.query([1.0] + [0.0] * 63, top_k=5)
        self.assertEqual(len(res), 0, "非 default 租户必须看不到老扁平路径数据")


if __name__ == "__main__":
    unittest.main()
