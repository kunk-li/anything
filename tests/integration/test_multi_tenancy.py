# -*- coding: utf-8 -*-
"""
Task #33 多租户集成测试 (跨模块端到端)

跟单元测试的区别:
    - 单元测试 (data_layer/*/tests/test_impl.py) 各自验证自己模块的 tenant_id 字段 / 目录分区 / 配额
    - 本测试同时持 vector_db / document_store / state_store 三个实例,
      模拟"tenant A 写所有三档数据 -> tenant B 拿出干净实例去读 -> 应 0 串扰"

设计参考 docs/multi-tenancy-design.md §12.2。
"""

from __future__ import annotations

import os

# 必须在 import 业务模块之前设置, 否则 build_basic_deps secrets fail-fast
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# 让脚本被 unittest discover 时也能 import 项目源
_ROOT = Path(__file__).resolve().parent.parent.parent
for layer in ("basic_support", "data_layer", "business", "interface", "application", "run", "."):
    p = str(_ROOT / layer) if layer != "." else str(_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import faiss  # noqa: F401
    FAISS_AVAILABLE = True
except Exception:
    FAISS_AVAILABLE = False

from vector_db_module.core.impl import FaissVectorDB
from vector_db_module.config.config import VectorDBConfig
from document_store_module.core.impl import LocalDocumentStore
from document_store_module.utils.tool_functions import calculate_content_hash
from state_store_module.core.impl import LocalStateStore


class _TestVectorDBConfig(VectorDBConfig):
    """测试用 cfg: 绕过 ConfigManager 直接喂参数"""

    def __init__(self, dim: int, store_dir: str):
        self._dim = dim
        self._dir = store_dir

    def get_vector_dimension(self) -> int:
        return int(self._dim)

    def get_local_store_dir(self) -> str:
        return str(self._dir)

    def get_vector_db_type(self) -> str:
        return "faiss"


@unittest.skipUnless(FAISS_AVAILABLE, "faiss not installed; integration test skipped")
class TestMultiTenancyEnd2End(unittest.TestCase):
    """跨模块端到端: tenant A 写完所有数据 → tenant B 全部读不到"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mt_integration_")
        self.vector_dir = os.path.join(self.tmpdir, "vector_store")
        self.state_dir = os.path.join(self.tmpdir, "state_store")
        os.makedirs(self.vector_dir, exist_ok=True)
        os.makedirs(self.state_dir, exist_ok=True)
        # document_store 在 default 配置目录, tenant_id 做唯一区分
        self.tid_a = "mt-tenant-a"
        self.tid_b = "mt-tenant-b"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        # 清理 document_store 在默认目录下的两个 tenant 子目录
        try:
            from document_store_module.config.config import DocumentStoreConfig
            base = DocumentStoreConfig().storage_dir
            for tid in (self.tid_a, self.tid_b):
                shutil.rmtree(os.path.join(base, tid), ignore_errors=True)
                shutil.rmtree(os.path.join("backup", tid), ignore_errors=True)
        except Exception:
            pass

    # ---------- vector_db ----------

    def test_vector_db_tenant_a_writes_b_reads_zero(self):
        """vector_db: tenant A 写向量, tenant B 同 query 应 0 命中"""
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.vector_dir)
        db_a = FaissVectorDB(cfg=cfg, tenant_id=self.tid_a)
        db_a.upsert_vectors([
            {
                "vector_id": "va1",
                "embedding": [1.0] + [0.0] * 63,
                "metadata": {"doc_id": "da", "chunk_id": "ca1", "text": "tenant-a secret"},
            },
            {
                "vector_id": "va2",
                "embedding": [0.0, 1.0] + [0.0] * 62,
                "metadata": {"doc_id": "da", "chunk_id": "ca2", "text": "tenant-a public"},
            },
        ])
        # 物理目录确认隔离 (P7 后持久化为单一 sqlite)
        self.assertTrue(os.path.exists(os.path.join(self.vector_dir, self.tid_a, "vectors.sqlite3")))

        # tenant B 来读 — 同方向 query 应该 0 命中
        db_b = FaissVectorDB(cfg=cfg, tenant_id=self.tid_b)
        res = db_b.query([1.0] + [0.0] * 63, top_k=5)
        self.assertEqual(len(res), 0, "tenant-b 不应该读到 tenant-a 的向量")

        # tenant A 自己读应该能拿到
        res_a = db_a.query([1.0] + [0.0] * 63, top_k=5)
        self.assertGreaterEqual(len(res_a), 1)
        self.assertEqual(res_a[0]["vector_id"], "va1")

    # ---------- document_store ----------

    def test_document_store_tenant_a_writes_b_cant_get(self):
        """document_store: tenant A 写文档, tenant B get_document 应返回 None"""
        store_a = LocalDocumentStore(tenant_id=self.tid_a)
        content = "tenant-a private document content"
        doc = store_a.create_document(content, "a.md", "md", calculate_content_hash(content))
        self.assertTrue(store_a.save_document(doc))
        doc_id = doc["doc_id"]

        # tenant B 实例
        store_b = LocalDocumentStore(tenant_id=self.tid_b)
        # 跨 tenant 用 doc_id 反查 —— 必须返回 None (跟 DOCUMENT_NOT_FOUND 一致)
        got_b = store_b.get_document(doc_id)
        self.assertIsNone(got_b, "tenant-b 不应该读到 tenant-a 的文档")

        # tenant A 自己能读
        got_a = store_a.get_document(doc_id)
        self.assertIsNotNone(got_a)
        self.assertEqual(got_a["content"], content)

    # ---------- state_store ----------

    def test_state_store_tenant_a_writes_b_gets_none(self):
        """state_store: tenant A 写 session 状态, tenant B 用同 session_id 拿应是 None"""
        # 用绝对路径 store_dir 配置, 避免依赖 yaml
        store_a = LocalStateStore(store_dir=self.state_dir, tenant_id=self.tid_a)
        store_b = LocalStateStore(store_dir=self.state_dir, tenant_id=self.tid_b)

        session_id = "session_shared_id_001"
        store_a.save_state(session_id, {"task": "tenant-a private state", "events": []})

        # tenant B 同 session_id 读, 应 None (按 tenant 切目录, 文件不在 B 的目录)
        state_b = store_b.get_state(session_id)
        self.assertIsNone(state_b, "tenant-b 不应该读到 tenant-a 的 state")

        # tenant A 自己读得到
        state_a = store_a.get_state(session_id)
        self.assertIsNotNone(state_a)
        self.assertEqual(state_a["task"], "tenant-a private state")

    # ---------- 跨三层一致性 ----------

    def test_tenant_id_charset_validation_consistent(self):
        """三个模块对 tenant_id 字符集校验必须一致 (深度防御 §9.2)"""
        bad_tids = ("Acme Corp", "../../etc", "ab", "x" * 33, "tenant.a", "tenant id")
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.vector_dir)

        for bad in bad_tids:
            with self.assertRaises((ValueError, Exception), msg=f"vector_db should reject {bad!r}"):
                FaissVectorDB(cfg=cfg, tenant_id=bad)
            with self.assertRaises((ValueError, Exception), msg=f"document_store should reject {bad!r}"):
                LocalDocumentStore(tenant_id=bad)
            with self.assertRaises((ValueError, Exception), msg=f"state_store should reject {bad!r}"):
                LocalStateStore(store_dir=self.state_dir, tenant_id=bad)

    def test_three_tenants_no_crosstalk(self):
        """三个 tenant 各写各的, 互相读不到 (验证非默认 fallback 防越权)"""
        cfg = _TestVectorDBConfig(dim=64, store_dir=self.vector_dir)
        emb_t1 = [1.0] + [0.0] * 63
        emb_t2 = [0.0, 1.0] + [0.0] * 62
        emb_t3 = [0.0, 0.0, 1.0] + [0.0] * 61

        for tid, emb in (("mt-tenant-x", emb_t1), ("mt-tenant-y", emb_t2), ("mt-tenant-z", emb_t3)):
            db = FaissVectorDB(cfg=cfg, tenant_id=tid)
            db.upsert_vectors([{
                "vector_id": f"v_{tid}",
                "embedding": emb,
                "metadata": {"doc_id": f"d_{tid}", "chunk_id": "c1"},
            }])

        # 每个 tenant 只能读自己的
        db_x = FaissVectorDB(cfg=cfg, tenant_id="mt-tenant-x")
        res_x = db_x.query(emb_t1, top_k=10)
        self.assertEqual([r["vector_id"] for r in res_x], ["v_mt-tenant-x"])

        db_y = FaissVectorDB(cfg=cfg, tenant_id="mt-tenant-y")
        res_y = db_y.query(emb_t1, top_k=10)  # 用 X 方向 query
        # Y 自己只有 1 个向量 (emb_t2 方向), 跟 emb_t1 余弦相似度 0
        ids_y = {r["vector_id"] for r in res_y}
        self.assertIn("v_mt-tenant-y", ids_y)
        self.assertNotIn("v_mt-tenant-x", ids_y)
        self.assertNotIn("v_mt-tenant-z", ids_y)


@unittest.skipUnless(FAISS_AVAILABLE, "faiss not installed")
class TestMultiTenancyConcurrent(unittest.TestCase):
    """模拟多个 tenant 实例并发持有, 验证内存中 ContextVar 不会泄漏"""

    def test_contextvar_isolation_across_data_layer_calls(self):
        """直接调数据层 (不经 ApiService middleware) 时 ContextVar 默认 None"""
        from observability_module import get_current_tenant, set_current_tenant, reset_current_tenant

        self.assertIsNone(get_current_tenant())

        # 在 'tenant-a' 上下文里建一个 vector_db, ContextVar 状态由调用方控制
        tmpdir = tempfile.mkdtemp(prefix="mt_ctxvar_")
        try:
            cfg = _TestVectorDBConfig(dim=8, store_dir=tmpdir)
            t = set_current_tenant("mt-tenant-a")
            try:
                # FaissVectorDB.__init__ 不会读 ContextVar, 但本测试确认它跑得通
                db = FaissVectorDB(cfg=cfg, tenant_id="mt-tenant-a")
                self.assertEqual(db.tenant_id, "mt-tenant-a")
                self.assertEqual(get_current_tenant(), "mt-tenant-a")
            finally:
                reset_current_tenant(t)
            self.assertIsNone(get_current_tenant())
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
