import os
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
        info_path = os.path.join(self.tmpdir, f"{doc['doc_id']}.info.json")
        os.remove(info_path)

        zombies = self.store.identify_zombie_files(threshold_days=0)
        self.assertTrue(any(z.get("reason") == "missing_info_file" for z in zombies))

        res = self.store.clean_zombie_files(threshold_days=0, backup=False)
        self.assertGreaterEqual(res["total"], 1)
        self.assertIsNone(self.store.get_document(doc["doc_id"]))

if __name__ == "__main__":
    unittest.main()
