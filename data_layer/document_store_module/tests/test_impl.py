import os

# 单独跑本测试时启用 dev mode 避免 build_basic_deps 触发 secrets fail-fast
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import shutil
import tempfile
import unittest

from document_store_module.core.impl import LocalDocumentStore
from document_store_module.utils.tool_functions import calculate_content_hash, is_uuid4


class TestLocalDocumentStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # inject env-based config for fallback ConfigManager
        os.environ["DOCUMENT_STORE_DOCUMENT_STORE_STORAGE_DIR"] = self.tmpdir
        os.environ["DOCUMENT_STORE_DOCUMENT_STORE_BACKUP_DIR"] = os.path.join(self.tmpdir, "backup")
        self.store = LocalDocumentStore()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("DOCUMENT_STORE_DOCUMENT_STORE_STORAGE_DIR", None)
        os.environ.pop("DOCUMENT_STORE_DOCUMENT_STORE_BACKUP_DIR", None)

    def test_find_doc_id_by_hash(self):
        """P2 查重: 只认 content_hash, 命中返已有 doc_id"""
        content = "dedup me"
        h = calculate_content_hash(content, "md5")
        doc = self.store.create_document(content, "a.txt", "txt", h)
        self.store.save_document(doc)
        self.assertEqual(self.store.find_doc_id_by_hash(h), doc["doc_id"])
        self.assertEqual(self.store.find_doc_id_by_hash(h.upper()), doc["doc_id"])  # 大小写不敏感
        self.assertIsNone(self.store.find_doc_id_by_hash("0" * 32))
        self.assertIsNone(self.store.find_doc_id_by_hash(""))
        # 删除后 hash 映射同步清掉, 不再命中
        self.store.delete_document(doc["doc_id"])
        self.assertIsNone(self.store.find_doc_id_by_hash(h))

    def test_crlf_content_roundtrip_char_exact(self):
        """P8 偏移一致性: \\r\\n 内容入库归一化, 落盘读回与 create 返回的串逐字符一致
        — chunk 偏移基于 create 返回串计算, 读回串必须相同否则按偏移取文错位"""
        raw = "line1\r\nline2\r\nline3\rtail\n中文行\r\n"
        doc = self.store.create_document(raw, "c.md", "md", calculate_content_hash(raw))
        normalized = doc["content"]
        self.assertNotIn("\r", normalized)
        self.assertTrue(self.store.save_document(doc))
        got = self.store.get_document(doc["doc_id"])
        self.assertEqual(got["content"], normalized)  # 逐字符一致
        # 切片语义可用 (模拟按偏移取文)
        idx = normalized.find("line2")
        self.assertEqual(got["content"][idx:idx + 5], "line2")

    def test_env_override_isolates_storage_dir(self):
        """setUp 设的 env 隔离必须真生效 — 历史上 get_config 不读 env, 测试
        一直写到 CWD/documents 污染仓库 (P13 修复的回归防护)"""
        self.assertTrue(self.store.base_storage_dir.startswith(self.tmpdir))

    def test_create_and_save_and_get(self):
        content = "hello world"
        file_name = "a.md"
        file_type = "md"
        content_hash = calculate_content_hash(content, "md5")

        doc = self.store.create_document(content, file_name, file_type, content_hash)
        self.assertTrue(is_uuid4(doc["doc_id"]))
        self.assertTrue(self.store.save_document(doc))

        got = self.store.get_document(doc["doc_id"])
        self.assertIsNotNone(got)
        self.assertEqual(got["content"], content)
        self.assertEqual(got["file_type"], "md")

    def test_update_document(self):
        content = "v1"
        doc = self.store.create_document(content, "a.txt", "txt", calculate_content_hash(content))
        self.assertTrue(self.store.save_document(doc))

        new_content = "v2"
        new_hash = calculate_content_hash(new_content)
        self.assertTrue(self.store.update_document(doc["doc_id"], new_content, new_hash))

        got = self.store.get_document(doc["doc_id"])
        self.assertEqual(got["content"], new_content)
        self.assertEqual(got["content_hash"], new_hash)

    def test_delete_document(self):
        content = "to delete"
        doc = self.store.create_document(content, "a.txt", "txt", calculate_content_hash(content))
        self.assertTrue(self.store.save_document(doc))
        self.assertTrue(self.store.delete_document(doc["doc_id"]))
        self.assertIsNone(self.store.get_document(doc["doc_id"]))

    def test_duplicate_check(self):
        content = "dup"
        h = calculate_content_hash(content)
        doc = self.store.create_document(content, "a.txt", "txt", h)
        self.assertTrue(self.store.save_document(doc))

        is_dup, dup_id = self.store.check_duplicate_file(h, "a.txt", "txt", len(content.encode("utf-8")))
        self.assertTrue(is_dup)
        self.assertEqual(dup_id, doc["doc_id"])

        is_dup2, dup_id2 = self.store.check_duplicate_file(h, "b.txt", "txt", len(content.encode("utf-8")))
        self.assertTrue(is_dup2)
        self.assertEqual(dup_id2, doc["doc_id"])

        is_dup3, dup_id3 = self.store.check_duplicate_file(h, "b.txt", "txt", len(content.encode("utf-8")), force_save=True)
        self.assertFalse(is_dup3)
        self.assertIsNone(dup_id3)

    def test_zombie_identify_and_clean_type4_missing_info(self):
        content = "zombie"
        doc = self.store.create_document(content, "a.txt", "txt", calculate_content_hash(content))
        self.assertTrue(self.store.save_document(doc))

        # remove info file to create type4
        info_path = doc["info_file_path"]
        os.remove(info_path)

        zombies = self.store.identify_zombie_files(threshold_days=0)
        self.assertTrue(any(z.get("reason") == "missing_info_file" for z in zombies))

        res = self.store.clean_zombie_files(threshold_days=0, backup=False)
        self.assertGreaterEqual(res["total"], 1)
        self.assertIsNone(self.store.get_document(doc["doc_id"]))

    # ============ Task #33 PR3a: tenant_id 接口扩展 ============

    def test_tenant_id_default(self):
        # setUp 已构造 store(无 tenant_id)
        self.assertEqual(self.store.tenant_id, "default")

    def test_tenant_id_specified(self):
        store = LocalDocumentStore(tenant_id="tenant-a")
        self.assertEqual(store.tenant_id, "tenant-a")

    def test_tenant_id_invalid_charset_rejected(self):
        for bad in ("Acme Corp", "../../etc", "ab", "x" * 33, 123, "tenant.id"):
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                LocalDocumentStore(tenant_id=bad)


class TestQuotaDoc(unittest.TestCase):
    """Task #33 PR4b: quotas.<tid>.max_documents 配额硬限

    每个 case 用唯一 tenant_id 保证 hash_doc_map 隔离 (env var override
    在当前 ConfigManager 不被支持, 不能用 tmpdir 隔离 storage_dir)。
    """

    _COUNTER = 0

    def setUp(self):
        # 唯一 tenant_id, 保证 hash_doc_map / 文件不被其他测试污染
        type(self)._COUNTER += 1
        self.tid = f"q-test-{type(self)._COUNTER:03d}"
        self.tmpdir = tempfile.mkdtemp()
        self.store = LocalDocumentStore(tenant_id=self.tid)

    def tearDown(self):
        # 清理本 tenant 的子目录
        try:
            shutil.rmtree(self.store.storage_dir, ignore_errors=True)
            shutil.rmtree(self.store.backup_dir, ignore_errors=True)
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patch_quota(self, limit):
        original_get = self.store.config_manager.get_config
        tenant_id = self.tid

        def patched(key, default=None):
            if key == f"quotas.{tenant_id}.max_documents":
                return limit
            return original_get(key, default)

        self.store.config_manager.get_config = patched

    def _make_and_save(self, content="hello"):
        doc = self.store.create_document(
            content, "a.md", "md", calculate_content_hash(content)
        )
        return self.store.save_document(doc)

    def test_quota_not_configured_no_limit(self):
        """没配 quota -> 不限制"""
        for i in range(5):
            self.assertTrue(self._make_and_save(content=f"v{i}"))

    def test_quota_within_limit_passes(self):
        """quota=3, 写 2 个 -> OK"""
        self._patch_quota(limit=3)
        self.assertTrue(self._make_and_save(content="v1"))
        self.assertTrue(self._make_and_save(content="v2"))

    def test_quota_exceeded_raises(self):
        """quota=2, 写第 3 个 (新 content_hash) -> QUOTA_DOC_EXCEEDED"""
        from document_store_module.core.impl import DocumentStoreException
        self._patch_quota(limit=2)
        self.assertTrue(self._make_and_save(content="v1"))
        self.assertTrue(self._make_and_save(content="v2"))
        with self.assertRaises(DocumentStoreException) as ctx:
            self._make_and_save(content="v3")
        self.assertEqual(ctx.exception.code, "QUOTA_DOC_EXCEEDED")

    def test_quota_same_content_hash_not_counted(self):
        """同 content_hash 视为更新, 不算新增, 不查 quota"""
        self._patch_quota(limit=1)
        self.assertTrue(self._make_and_save(content="v1"))
        # 再写一次同样内容 (相同 hash) - 不应被 quota 拦截
        self.assertTrue(self._make_and_save(content="v1"))

    def test_quota_zero_rejects_all_new(self):
        """quota=0 表示运维有意冻结, 拒绝任何新文档"""
        from document_store_module.core.impl import DocumentStoreException
        self._patch_quota(limit=0)
        with self.assertRaises(DocumentStoreException) as ctx:
            self._make_and_save(content="anything")
        self.assertEqual(ctx.exception.code, "QUOTA_DOC_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
