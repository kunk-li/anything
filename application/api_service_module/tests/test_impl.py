"""API服务模块测试。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api_service_module.config.config import ApiServiceConfig
from api_service_module.core.impl import ApiService


class MockHandler:
    """模拟请求处理器。"""

    def handle(self, request):
        return {
            "code": "SUCCESS",
            "message": "ok",
            "data": {
                "echo": request,
            },
            "trace_id": request.get("session_id", "trace"),
            "retryable": False,
            "details": None,
        }


class TestApiService(unittest.TestCase):
    """API 服务模块单元测试。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = ApiServiceConfig(
            auth_enabled=False,
            upload_dir=self.temp_dir.name,
            index_result_dir=self.temp_dir.name,
        )
        self.service = ApiService(handler=MockHandler(), config=self.config)
        self.client = TestClient(self.service.app)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_invoke_success(self):
        response = self.client.post("/invoke", json={"type": "rag", "query": "test", "top_k": 3})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], "SUCCESS")
        self.assertEqual(payload["data"]["echo"]["query"], "test")

    def test_invoke_missing_query(self):
        response = self.client.post("/invoke", json={"type": "rag"})
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["code"], "PARAM_MISSING")

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], "SUCCESS")

    def test_upload_document(self):
        response = self.client.post(
            "/documents/upload",
            files={"file": ("demo.txt", b"hello world", "text/plain")},
        )
        self.assertEqual(response.status_code, 200)
        stored_path = response.json()["data"]["stored_path"]
        self.assertTrue(Path(stored_path).exists())

    def test_build_index_success(self):
        source_dir = Path(self.temp_dir.name) / "docs"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "a.txt").write_text("demo", encoding="utf-8")
        response = self.client.post(
            "/index/build",
            json={"source_type": "local_folder", "source_path": str(source_dir), "chunking": {"chunk_size_tokens": 400}},
        )
        self.assertEqual(response.status_code, 200)
        job_id = response.json()["data"]["job_id"]
        job_resp = self.client.get(f"/index/job/{job_id}")
        self.assertEqual(job_resp.status_code, 200)
        self.assertEqual(job_resp.json()["data"]["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
