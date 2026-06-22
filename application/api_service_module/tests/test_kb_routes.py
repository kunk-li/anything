# -*- coding: utf-8 -*-
"""
KB 路由测试 (code-review 修复回归)

覆盖:
    - POST /kb/{id}/docs 的 'added' 计数只数真插入的成员,
      INSERT OR IGNORE 命中既有成员不应再计入 (旧版无脑 +1 over-report)
"""

from __future__ import annotations

import os

os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api_service_module.core.impl import ApiService


class _Handler:
    def handle(self, request, trace_id=None):
        return {"code": "SUCCESS", "message": "ok", "data": {}, "trace_id": trace_id,
                "retryable": False, "details": None}


class TestKbAddDocsCount(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._saved_root = os.environ.get("ANYTHING_DATA_ROOT")
        os.environ["ANYTHING_DATA_ROOT"] = str(self.tmp)
        self.service = ApiService(handler=_Handler())
        self.client = TestClient(self.service.app)

    def tearDown(self):
        if self._saved_root is None:
            os.environ.pop("ANYTHING_DATA_ROOT", None)
        else:
            os.environ["ANYTHING_DATA_ROOT"] = self._saved_root

    def _make_kb(self) -> str:
        r = self.client.post("/kb", json={"name": "kb-test"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["data"]["id"]

    def test_added_counts_only_real_insertions(self):
        kb_id = self._make_kb()

        # 首次加 3 个全新成员 → added=3
        r = self.client.post(f"/kb/{kb_id}/docs", json={"doc_ids": ["a", "b", "c"]})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertEqual(data["added"], 3)
        self.assertEqual(data["requested"], 3)

        # 重复加: 'a','b' 已存在 (IGNORE), 只有 'd' 是新 → added=1, requested=3
        r = self.client.post(f"/kb/{kb_id}/docs", json={"doc_ids": ["a", "b", "d"]})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertEqual(data["added"], 1)
        self.assertEqual(data["requested"], 3)

        # 全部重复 → added=0
        r = self.client.post(f"/kb/{kb_id}/docs", json={"doc_ids": ["a", "b", "c", "d"]})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertEqual(data["added"], 0)
        self.assertEqual(data["requested"], 4)

        # 成员总数核对: 共 4 个唯一 doc
        r = self.client.get(f"/kb/{kb_id}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["doc_count"], 4)


if __name__ == "__main__":
    unittest.main()
