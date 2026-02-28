import unittest
from document_store_module.core.base import BaseDocumentStore


class TestBaseDocumentStore(unittest.TestCase):
    def test_is_abstract(self):
        self.assertTrue(hasattr(BaseDocumentStore, "__abstractmethods__"))
        self.assertGreater(len(BaseDocumentStore.__abstractmethods__), 0)


if __name__ == "__main__":
    unittest.main()
