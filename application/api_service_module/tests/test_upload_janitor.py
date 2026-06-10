# -*- coding: utf-8 -*-
"""
P1 uploads 保留期清理器测试

覆盖 _clean_uploads_once 的清理边界:
    - 已索引 + 超期 + 非图片原件 → 删
    - 未被索引引用的文件 → 保留 (可能是索引失败的孤本)
    - 图片原件 → 保留 (image_describe 会话回读)
    - 未超期 → 保留
    - uploads/screenshots/ 超期截图 → 删
    - retention<=0 / 无 doc store 时 startup 不起线程
"""

from __future__ import annotations

import os

os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import tempfile
import time
import unittest
from pathlib import Path

from api_service_module.core.impl import ApiService


class _Handler:
    def handle(self, request, trace_id=None):
        return {"code": "SUCCESS", "message": "ok", "data": {}, "trace_id": trace_id,
                "retryable": False, "details": None}


class _StubStore:
    def __init__(self, referenced_paths):
        self._refs = referenced_paths

    def list_documents(self):
        return [{"doc_id": f"d{i}", "stored_path": p} for i, p in enumerate(self._refs)]


class _CfgOverride:
    def __init__(self, upload_dir, retention=0):
        self._m = {
            "api_service.upload_dir": str(upload_dir),
            "api_service.upload_retention_days": retention,
        }

    def get_config(self, key, default=None):
        return self._m.get(key, default)


def _old(path: Path, days: int = 40) -> None:
    ts = time.time() - days * 24 * 3600
    os.utime(path, (ts, ts))


class TestUploadJanitor(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.upload_dir = self.tmp / "uploads"
        (self.upload_dir / "screenshots").mkdir(parents=True)

        # 已索引 + 超期 + 非图片 → 应删
        self.indexed_old = self.upload_dir / "indexed_old.txt"
        self.indexed_old.write_text("x", encoding="utf-8")
        _old(self.indexed_old)
        # 已索引但未超期 → 保留
        self.indexed_fresh = self.upload_dir / "indexed_fresh.txt"
        self.indexed_fresh.write_text("x", encoding="utf-8")
        # 未被索引引用 + 超期 → 保留 (可能是索引失败孤本)
        self.orphan_old = self.upload_dir / "orphan_old.txt"
        self.orphan_old.write_text("x", encoding="utf-8")
        _old(self.orphan_old)
        # 已索引 + 超期 + 图片 → 保留
        self.image_old = self.upload_dir / "photo.png"
        self.image_old.write_bytes(b"\x89PNG")
        _old(self.image_old)
        # 超期截图 → 应删
        self.shot_old = self.upload_dir / "screenshots" / "shot1.png"
        self.shot_old.write_bytes(b"\x89PNG")
        _old(self.shot_old)
        # 未超期截图 → 保留
        self.shot_fresh = self.upload_dir / "screenshots" / "shot2.png"
        self.shot_fresh.write_bytes(b"\x89PNG")

        refs = [str(self.indexed_old), str(self.indexed_fresh), str(self.image_old)]
        self.service = ApiService(
            handler=_Handler(),
            document_store_factory=lambda tid: _StubStore(refs),
        )
        self.service.config = _CfgOverride(self.upload_dir, retention=30)

    def test_clean_uploads_once_boundaries(self):
        removed = self.service._clean_uploads_once(days=30)
        self.assertEqual(removed, {"indexed_originals": 1, "screenshots": 1})
        self.assertFalse(self.indexed_old.exists())     # 已索引+超期 → 删
        self.assertTrue(self.indexed_fresh.exists())    # 未超期 → 留
        self.assertTrue(self.orphan_old.exists())       # 无索引引用 → 留
        self.assertTrue(self.image_old.exists())        # 图片 → 留
        self.assertFalse(self.shot_old.exists())        # 超期截图 → 删
        self.assertTrue(self.shot_fresh.exists())       # 新截图 → 留

    def test_janitor_not_started_when_retention_off(self):
        import threading
        before = {t.name for t in threading.enumerate()}
        svc = ApiService(handler=_Handler(), document_store_factory=lambda tid: _StubStore([]))
        svc.config = _CfgOverride(self.upload_dir, retention=0)
        svc._start_upload_janitor()
        after = {t.name for t in threading.enumerate()}
        self.assertNotIn("upload-janitor", after - before)

    def test_janitor_not_started_without_doc_store(self):
        import threading
        svc = ApiService(handler=_Handler())
        svc.config = _CfgOverride(self.upload_dir, retention=30)
        svc._start_upload_janitor()
        self.assertNotIn("upload-janitor", {t.name for t in threading.enumerate()})


if __name__ == "__main__":
    unittest.main()
