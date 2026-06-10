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

    def test_delete_by_vector_ids(self):
        """真删除: 按 vector_id 删, 索引/映射同步收缩, 删后查不到"""
        self.db.upsert_vectors([
            {"vector_id": "v1", "embedding": [0.1] * 64, "metadata": {"doc_id": "d1", "chunk_id": "c1"}},
            {"vector_id": "v2", "embedding": [0.2] * 64, "metadata": {"doc_id": "d2", "chunk_id": "c2"}},
        ])
        self.assertTrue(self.db.delete(vector_ids=["v1"]))
        self.assertEqual(self.db.index.ntotal, 1)
        res = self.db.query([0.1] * 64, top_k=10)
        self.assertTrue(all(r["vector_id"] != "v1" for r in res))
        # 幂等: 再删同 id 返回 False 不抛错
        self.assertFalse(self.db.delete(vector_ids=["v1"]))

    def test_delete_by_doc_id_filter(self):
        """按 filters={doc_id} 删除整篇文档的向量 (DELETE /documents 路径)"""
        self.db.upsert_vectors([
            {"vector_id": "d1#c1", "embedding": [0.1] * 64, "metadata": {"doc_id": "d1", "chunk_id": "d1#c1"}},
            {"vector_id": "d1#c2", "embedding": [0.2] * 64, "metadata": {"doc_id": "d1", "chunk_id": "d1#c2"}},
            {"vector_id": "d2#c1", "embedding": [0.3] * 64, "metadata": {"doc_id": "d2", "chunk_id": "d2#c1"}},
        ])
        self.assertTrue(self.db.delete(filters={"doc_id": "d1"}))
        self.assertEqual(self.db.index.ntotal, 1)
        res = self.db.query([0.3] * 64, top_k=10)
        self.assertEqual([r["vector_id"] for r in res], ["d2#c1"])

    def test_delete_persists_across_reload(self):
        """删除后重载新实例, 删掉的向量不复活"""
        self.db.upsert_vectors([
            {"vector_id": "v1", "embedding": [0.1] * 64, "metadata": {"doc_id": "d1", "chunk_id": "c1"}},
            {"vector_id": "v2", "embedding": [0.2] * 64, "metadata": {"doc_id": "d2", "chunk_id": "c2"}},
        ])
        self.db.delete(vector_ids=["v1"])
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.tmp)
        db2 = FaissVectorDB(cfg=cfg)
        self.assertEqual(db2.index.ntotal, 1)
        res = db2.query([0.2] * 64, top_k=10)
        self.assertEqual([r["vector_id"] for r in res], ["v2"])

    def test_legacy_format_migration(self):
        """旧三件套 (gen1: meta.json 无 int_of) 自动迁移进 vectors.sqlite3, 数据不丢"""
        import json as _json
        import os as _os
        import numpy as _np
        import tempfile as _tempfile
        # 全新目录手写旧格式文件 (无 sqlite), 模拟老部署升级
        legacy_dir = _tempfile.mkdtemp()
        tenant_dir = _os.path.join(legacy_dir, "default")
        _os.makedirs(tenant_dir)
        emb = _np.asarray([[0.5] * 64], dtype="float32")
        _np.save(_os.path.join(tenant_dir, "embeddings.npy"), emb)
        with open(_os.path.join(tenant_dir, "meta.json"), "w", encoding="utf-8") as f:
            _json.dump({
                "id_map": ["v1"],
                "meta_map": {"v1": {"doc_id": "d1", "chunk_id": "c1"}},
            }, f, ensure_ascii=False)

        cfg = _TestVectorDBConfig(dim=64, store_dir=legacy_dir)
        db2 = FaissVectorDB(cfg=cfg)
        self.assertEqual(db2.index.ntotal, 1)
        res = db2.query([0.5] * 64, top_k=1)
        self.assertEqual(res[0]["vector_id"], "v1")
        # 迁移落到 sqlite, 旧文件保留原地作回滚备份
        self.assertTrue(_os.path.exists(db2.db_path))
        self.assertTrue(_os.path.exists(_os.path.join(tenant_dir, "meta.json")))
        # 迁移后支持删除, 且重载读 sqlite (不再回读旧文件)
        self.assertTrue(db2.delete(vector_ids=["v1"]))
        self.assertEqual(db2.index.ntotal, 0)
        db3 = FaissVectorDB(cfg=cfg)
        self.assertEqual(db3.index.ntotal, 0)

    def test_incremental_upsert_no_full_rewrite(self):
        """P7: 二次 upsert 只动新行 — 已有向量行内容不被改写 (增量语义)"""
        self.db.upsert_vectors([
            {"vector_id": "v1", "embedding": [0.1] * 64, "metadata": {"doc_id": "d1", "chunk_id": "c1"}},
        ])
        row1 = self.db._conn.execute(
            "SELECT int_id FROM vectors WHERE chunk_id='v1'").fetchone()
        self.db.upsert_vectors([
            {"vector_id": "v2", "embedding": [0.2] * 64, "metadata": {"doc_id": "d2", "chunk_id": "c2"}},
        ])
        row1_after = self.db._conn.execute(
            "SELECT int_id FROM vectors WHERE chunk_id='v1'").fetchone()
        self.assertEqual(row1, row1_after)  # v1 的 int_id 终身不变
        self.assertEqual(self.db.index.ntotal, 2)
        # 同 id 覆盖更新不膨胀
        self.db.upsert_vectors([
            {"vector_id": "v1", "embedding": [0.3] * 64, "metadata": {"doc_id": "d1", "chunk_id": "c1"}},
        ])
        self.assertEqual(self.db.index.ntotal, 2)
        n_rows = self.db._conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        self.assertEqual(n_rows, 2)

    def test_filter_membership_and_widening(self):
        """P15: filters 值为集合时做成员判定; 小子集命中不足自动放大候选不饿死"""
        items = []
        for i in range(50):
            items.append({
                "vector_id": f"d{i}#c1",
                "embedding": [0.01 * (i + 1)] * 64,
                "metadata": {"doc_id": f"d{i}", "chunk_id": f"d{i}#c1"},
            })
        self.db.upsert_vectors(items)
        # 只允许 2 个冷门 doc — 旧实现 oversample=top_k*10 可能全程不命中
        allowed = ["d3", "d27"]
        res = self.db.query([0.5] * 64, top_k=2, filters={"doc_id": allowed})
        self.assertEqual(len(res), 2)
        self.assertEqual({r["metadata"]["doc_id"] for r in res}, set(allowed))

    def test_clear(self):
        self.db.upsert_vectors([
            {"vector_id": "v1", "embedding": [0.1] * 64, "metadata": {"doc_id": "d1", "chunk_id": "c1"}},
        ])
        self.assertTrue(self.db.clear())
        self.assertEqual(self.db.index.ntotal, 0)
        self.assertEqual(self.db.query([0.1] * 64, top_k=5), [])

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
        # 验证物理目录隔离 (P7 后持久化为单一 sqlite)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "tenant-a", "vectors.sqlite3")))

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


@unittest.skipUnless(FAISS_AVAILABLE, "faiss not installed")
class TestQuotaStorage(unittest.TestCase):
    """Task #33 PR4b: quotas.<tid>.max_vector_store_mb 配额硬限"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = _TestVectorDBConfig(dim=64, store_dir=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_quota(self, db, quota_mb, tenant_id="default"):
        original_get = db.config_manager.get_config

        def patched(key, default=None):
            if key == f"quotas.{tenant_id}.max_vector_store_mb":
                return quota_mb
            return original_get(key, default)

        db.config_manager.get_config = patched

    def test_quota_not_configured_no_limit(self):
        """没配 quota -> 不限制, 可任意写入"""
        db = FaissVectorDB(cfg=self.cfg, tenant_id="default")
        ok = db.upsert_vectors([
            {"vector_id": f"v{i}", "embedding": [0.1] * 64,
             "metadata": {"doc_id": "d", "chunk_id": f"c{i}"}}
            for i in range(50)
        ])
        self.assertTrue(ok)

    def test_quota_within_limit_passes(self):
        """quota=10MB 写少量向量 -> 通过"""
        db = FaissVectorDB(cfg=self.cfg, tenant_id="default")
        self._patch_quota(db, quota_mb=10)
        ok = db.upsert_vectors([
            {"vector_id": "v1", "embedding": [0.1] * 64,
             "metadata": {"doc_id": "d", "chunk_id": "c1"}}
        ])
        self.assertTrue(ok)

    def test_quota_exceeded_raises(self):
        """quota=0.001MB (1KB) 写 1000 向量 (256KB) -> QUOTA_STORAGE_EXCEEDED"""
        db = FaissVectorDB(cfg=self.cfg, tenant_id="default")
        self._patch_quota(db, quota_mb=0.001)
        with self.assertRaises(VectorDBException) as ctx:
            db.upsert_vectors([
                {"vector_id": f"v{i}", "embedding": [0.1] * 64,
                 "metadata": {"doc_id": "d", "chunk_id": f"c{i}"}}
                for i in range(1000)
            ])
        self.assertEqual(ctx.exception.code, "QUOTA_STORAGE_EXCEEDED")

    def test_quota_per_tenant_isolated(self):
        """tenant-a 配额满, tenant-b 应不受影响"""
        db_a = FaissVectorDB(cfg=self.cfg, tenant_id="tenant-a")
        db_b = FaissVectorDB(cfg=self.cfg, tenant_id="tenant-b")
        # 只给 tenant-a 配 0.0001MB (几乎为 0), tenant-b 不 patch -> 无 quota
        self._patch_quota(db_a, quota_mb=0.0001, tenant_id="tenant-a")

        with self.assertRaises(VectorDBException) as ctx:
            db_a.upsert_vectors([
                {"vector_id": "va1", "embedding": [0.1] * 64,
                 "metadata": {"doc_id": "d", "chunk_id": "c1"}}
            ])
        self.assertEqual(ctx.exception.code, "QUOTA_STORAGE_EXCEEDED")

        # tenant-b 不受影响
        ok = db_b.upsert_vectors([
            {"vector_id": "vb1", "embedding": [0.1] * 64,
             "metadata": {"doc_id": "d", "chunk_id": "c1"}}
        ])
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
