# -*- coding: utf-8 -*-
"""
chunker_module 单元测试
"""

import unittest

from chunker_module import (
    estimate_tokens,
    normalize_text,
    split_by_natural_boundaries,
    chunk_document,
    build_upsert_items,
)


class TestEstimateTokens(unittest.TestCase):
    def test_empty_returns_one(self):
        self.assertEqual(estimate_tokens(""), 1)
        self.assertEqual(estimate_tokens(None), 1)

    def test_chars_div_four(self):
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("abcdefgh"), 2)
        # 12 / 4 = 3
        self.assertEqual(estimate_tokens("abcdefghijkl"), 3)

    def test_min_one(self):
        self.assertEqual(estimate_tokens("a"), 1)


class TestNormalizeText(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(normalize_text(None), "")

    def test_crlf_to_lf(self):
        self.assertEqual(normalize_text("a\r\nb"), "a\nb")
        self.assertEqual(normalize_text("a\rb"), "a\nb")

    def test_collapse_multiple_blank_lines(self):
        self.assertEqual(normalize_text("a\n\n\n\nb"), "a\n\nb")

    def test_strip(self):
        self.assertEqual(normalize_text("  hello  "), "hello")


class TestSplitByNaturalBoundaries(unittest.TestCase):
    def test_markdown_headings(self):
        text = "# H1\nhello\n\n## H2\nworld"
        parts = split_by_natural_boundaries(text)
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].startswith("# H1"))
        self.assertTrue(parts[1].startswith("## H2"))

    def test_paragraphs(self):
        text = "para one\n\npara two\n\npara three"
        parts = split_by_natural_boundaries(text)
        self.assertEqual(parts, ["para one", "para two", "para three"])

    def test_chinese_sentences(self):
        # regex 要求句号/分号后跟空白或换行才切分(实际边界判断条件)
        text = "第一句。\n第二句!\n第三句?"
        parts = split_by_natural_boundaries(text)
        self.assertGreaterEqual(len(parts), 2)

    def test_empty(self):
        self.assertEqual(split_by_natural_boundaries(""), [])


class TestChunkDocument(unittest.TestCase):
    def test_empty_content_returns_empty(self):
        chunks = chunk_document("d1", "", "f.md")
        self.assertEqual(chunks, [])

    def test_chunk_id_format(self):
        # 强制 doc_id#c000001 这种格式
        content = "# A\n" + ("x" * 100) + "\n\n# B\n" + ("y" * 100)
        chunks = chunk_document("doc-123", content, "f.md", chunk_size_tokens=20)
        self.assertGreater(len(chunks), 0)
        for c in chunks:
            self.assertTrue(c["chunk_id"].startswith("doc-123#c"))
            self.assertEqual(c["doc_id"], "doc-123")

    def test_required_meta_fields(self):
        content = "# A\nhello world this is a test paragraph one.\n\n# B\nanother section."
        chunks = chunk_document("d1", content, "test.md", source="test")
        self.assertGreater(len(chunks), 0)
        first = chunks[0]
        # 文档 11.1 节强制字段
        self.assertIn("doc_id", first)
        self.assertIn("chunk_id", first)
        self.assertIn("content", first)
        meta = first["meta"]
        for key in ("file_name", "source", "chunk_index", "start_char",
                    "end_char", "token_count_est"):
            self.assertIn(key, meta, f"meta 缺字段: {key}")
        self.assertEqual(meta["file_name"], "test.md")
        self.assertEqual(meta["source"], "test")

    def test_chunk_index_starts_at_1(self):
        content = "# A\ntext " * 50
        chunks = chunk_document("d1", content, "f.md", chunk_size_tokens=30)
        self.assertEqual(chunks[0]["meta"]["chunk_index"], 1)
        if len(chunks) > 1:
            self.assertEqual(chunks[1]["meta"]["chunk_index"], 2)

    def test_chunk_id_zero_padded(self):
        content = "# A\n" + "段落 " * 200
        chunks = chunk_document("d1", content, "f.md", chunk_size_tokens=30)
        # 推荐 6 位零填充: doc_id#c000001
        for c in chunks:
            suffix = c["chunk_id"].split("#c")[-1]
            self.assertEqual(len(suffix), 6)
            self.assertTrue(suffix.isdigit())


class TestBuildUpsertItems(unittest.TestCase):
    def test_metadata_required_fields(self):
        chunks = [{
            "doc_id": "d1",
            "chunk_id": "d1#c000001",
            "content": "hello",
            "meta": {
                "file_name": "f.md",
                "source": "test",
                "chunk_index": 1,
                "start_char": 0,
                "end_char": 5,
                "token_count_est": 2,
            },
        }]
        vectors = [[0.1, 0.2, 0.3]]
        items = build_upsert_items(chunks, vectors)

        self.assertEqual(len(items), 1)
        item = items[0]
        # vector_id 必须等于 chunk_id(文档 11.5 节)
        self.assertEqual(item["vector_id"], "d1#c000001")
        self.assertEqual(item["embedding"], [0.1, 0.2, 0.3])

        meta = item["metadata"]
        # 文档 11.4 节强制 metadata 字段
        for key in ("doc_id", "chunk_id", "file_name", "chunk_index"):
            self.assertIn(key, meta)
        # 额外字段(本实现增强)
        self.assertEqual(meta["content"], "hello")
        self.assertEqual(meta["start_char"], 0)


class TestChunkSchemaCompliance(unittest.TestCase):
    """守护测试: chunk_document 的输出必须符合 schema_module.Chunk 契约。

    这层校验放在测试侧而非运行时,运行时零开销;
    如果未来代码修改导致 chunk 字段缺失/格式错误,这里立即报错。
    """

    def test_all_chunks_validate_against_schema(self):
        from schema_module import Chunk

        content = (
            "# 标题 1\n" + ("段落一段落一段落一 " * 30) + "\n\n"
            "# 标题 2\n" + ("段落二段落二段落二 " * 30)
        )
        chunks = chunk_document(
            doc_id="doc-xyz",
            content=content,
            file_name="test.md",
            source="test_src",
            chunk_size_tokens=50,
        )
        self.assertGreater(len(chunks), 0)
        for c in chunks:
            # 任一字段缺失/类型错误,Chunk.model_validate 会抛 ValidationError
            schema_obj = Chunk.model_validate(c)
            # 顺手验证关键约束
            self.assertTrue(schema_obj.chunk_id.startswith("doc-xyz#c"))
            self.assertEqual(schema_obj.meta.file_name, "test.md")
            self.assertEqual(schema_obj.meta.source, "test_src")
            self.assertGreaterEqual(schema_obj.meta.chunk_index, 1)
            self.assertGreaterEqual(schema_obj.meta.end_char, schema_obj.meta.start_char)


if __name__ == "__main__":
    unittest.main()
