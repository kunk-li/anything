# -*- coding: utf-8 -*-
"""
P3-P6/P14 删除链路彻底化 + 索引真重建 测试

覆盖:
    - DELETE /documents/{doc_id}: document_store + vector_db + BM25 + uploads 原件
      + kb_doc 关联五处一次清, data 字段如实回报各处结果
    - POST /index/build 真重建 (后台 job) + GET /index/job/{id} 状态
    - rebuild_runner 未注入时 501 (不再是假 job_id)
"""

from __future__ import annotations

import os

os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api_service_module.core.impl import ApiService


class _Handler:
    def handle(self, request, trace_id=None):
        return {"code": "SUCCESS", "message": "ok", "data": {}, "trace_id": trace_id,
                "retryable": False, "details": None}


class _StubStore:
    def __init__(self, stored_path=None):
        self.stored_path = stored_path
        self.deleted = []

    def read_info_file(self, doc_id):
        return {"doc_id": doc_id, "stored_path": self.stored_path}

    def delete_document(self, doc_id):
        self.deleted.append(doc_id)
        return True


class _StubVectorDB:
    def __init__(self):
        self.deleted_filters = []

    def delete(self, vector_ids=None, filters=None):
        self.deleted_filters.append(filters)
        return True


class _CfgOverride:
    """只覆盖 upload_dir, 其余 key 全返默认值的 config 桩"""

    def __init__(self, upload_dir):
        self._m = {"api_service.upload_dir": str(upload_dir)}

    def get_config(self, key, default=None):
        return self._m.get(key, default)


class TestDeleteChain(unittest.TestCase):

    def setUp(self):
        from rag_module.extensions import BM25Retriever
        self.tmp = Path(tempfile.mkdtemp())
        # kb.sqlite3 数据根指到临时目录 (kb._get_db_path 读 ANYTHING_DATA_ROOT)
        self._saved_root = os.environ.get("ANYTHING_DATA_ROOT")
        os.environ["ANYTHING_DATA_ROOT"] = str(self.tmp)

        self.upload_dir = self.tmp / "uploads"
        self.upload_dir.mkdir()
        self.orig_file = self.upload_dir / "orig.txt"
        self.orig_file.write_text("hello", encoding="utf-8")

        kb_db = self.tmp / "kb.sqlite3"
        conn = sqlite3.connect(str(kb_db))
        conn.execute("CREATE TABLE kb_doc (kb_id TEXT, doc_id TEXT, added_at TEXT)")
        conn.execute("INSERT INTO kb_doc VALUES ('kb1', 'd1', 'now')")
        conn.execute("INSERT INTO kb_doc VALUES ('kb1', 'd2', 'now')")
        conn.commit()
        conn.close()

        self.bm25 = BM25Retriever()
        self.bm25.add_chunks([
            {"chunk_id": "d1#c1", "doc_id": "d1", "content": "python web"},
            {"chunk_id": "d1#c2", "doc_id": "d1", "content": "python async"},
            {"chunk_id": "d2#c1", "doc_id": "d2", "content": "rust safety"},
        ])
        self.bm25_path = str(self.tmp / "bm25.json")

        self.store = _StubStore(stored_path=str(self.orig_file))
        self.vec = _StubVectorDB()
        self.service = ApiService(
            handler=_Handler(),
            document_store_factory=lambda tid: self.store,
            vector_db=self.vec,
            bm25_retriever=self.bm25,
            bm25_index_path=self.bm25_path,
        )
        # 路由在请求期读 self.config 拿 upload_dir, 构造后替换不影响已初始化的鉴权等
        self.service.config = _CfgOverride(self.upload_dir)
        self.client = TestClient(self.service.app)

    def tearDown(self):
        if self._saved_root is None:
            os.environ.pop("ANYTHING_DATA_ROOT", None)
        else:
            os.environ["ANYTHING_DATA_ROOT"] = self._saved_root

    def test_delete_clears_all_five_stores(self):
        r = self.client.delete("/documents/d1")
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        self.assertTrue(data["deleted_from_document_store"])
        self.assertTrue(data["deleted_from_vector_db"])
        self.assertEqual(data["bm25_chunks_removed"], 2)
        self.assertTrue(data["deleted_upload_file"])
        self.assertEqual(data["kb_links_removed"], 1)
        self.assertEqual(data["warnings"], [])
        # 实际状态核对
        self.assertEqual(self.store.deleted, ["d1"])
        self.assertEqual(self.vec.deleted_filters, [{"doc_id": "d1"}])
        self.assertEqual(self.bm25.size, 1)
        self.assertFalse(self.orig_file.exists())
        conn = sqlite3.connect(str(self.tmp / "kb.sqlite3"))
        left = conn.execute("SELECT doc_id FROM kb_doc").fetchall()
        conn.close()
        self.assertEqual(left, [("d2",)])
        # BM25 摘除后已持久化
        self.assertTrue(os.path.exists(self.bm25_path))

    def test_delete_missing_doc_returns_404_document_not_found(self):
        """document_store 没这条 doc (delete_document 返 False) —
        应 404 + code DOCUMENT_NOT_FOUND (与全仓 not_found 桶一致), 不再是 200/NOT_FOUND"""
        self.store.delete_document = lambda doc_id: False
        r = self.client.delete("/documents/dnope")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "DOCUMENT_NOT_FOUND")
        self.assertFalse(r.json()["data"]["deleted_from_document_store"])

    def test_delete_upload_file_outside_upload_dir_not_touched(self):
        """info.stored_path 指到 upload_dir 外 (比如被篡改) — 一律不删"""
        outside = self.tmp / "outside.txt"
        outside.write_text("keep me", encoding="utf-8")
        self.store.stored_path = str(outside)
        r = self.client.delete("/documents/d1")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["data"]["deleted_upload_file"])
        self.assertTrue(outside.exists())


class TestIndexRebuild(unittest.TestCase):

    def _service(self, rebuild_runner=None):
        return ApiService(handler=_Handler(), rebuild_runner=rebuild_runner)

    def test_rebuild_not_wired_returns_501(self):
        client = TestClient(self._service(rebuild_runner=None).app)
        r = client.post("/index/build")
        self.assertEqual(r.status_code, 501)
        self.assertEqual(r.json()["code"], "SERVICE_UNAVAILABLE")

    def test_rebuild_job_lifecycle(self):
        def runner(progress_cb=None):
            if progress_cb:
                progress_cb(2, 2)
            return {"code": "SUCCESS",
                    "data": {"total_docs": 2, "rebuilt_docs": 2, "total_chunks": 5, "skipped": []}}

        client = TestClient(self._service(rebuild_runner=runner).app)
        r = client.post("/index/build")
        self.assertEqual(r.status_code, 200)
        job_id = r.json()["data"]["job_id"]
        # 后台线程, 轮询到完成 (上限 5s)
        deadline = time.time() + 5
        status = None
        while time.time() < deadline:
            jr = client.get(f"/index/job/{job_id}")
            status = jr.json()["data"]["status"]
            if status in ("SUCCESS", "FAILED"):
                break
            time.sleep(0.05)
        self.assertEqual(status, "SUCCESS")
        data = jr.json()["data"]
        self.assertEqual(data["progress"], {"done": 2, "total": 2})
        self.assertEqual(data["result"]["rebuilt_docs"], 2)

    def test_rebuild_failure_reported(self):
        def runner(progress_cb=None):
            raise RuntimeError("boom")

        client = TestClient(self._service(rebuild_runner=runner).app)
        job_id = client.post("/index/build").json()["data"]["job_id"]
        deadline = time.time() + 5
        while time.time() < deadline:
            data = client.get(f"/index/job/{job_id}").json()["data"]
            if data["status"] in ("SUCCESS", "FAILED"):
                break
            time.sleep(0.05)
        self.assertEqual(data["status"], "FAILED")
        self.assertIn("boom", data["error"])

    def test_unknown_job_404(self):
        client = TestClient(self._service(rebuild_runner=lambda progress_cb=None: {}).app)
        r = client.get("/index/job/job_nope")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "JOB_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
