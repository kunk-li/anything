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
        # P8: 全文不再进 metadata (单源在 document_store, 检索后按偏移取文);
        # 引用回溯靠 start/end_char
        self.assertNotIn("content", meta)
        self.assertEqual(meta["start_char"], 0)
        self.assertIn("end_char", meta)


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


class TestChunkOffsets(unittest.TestCase):
    def test_repeated_paragraph_offsets_do_not_snap_back(self):
        """回归: 重复出现的段落, 各 chunk 的 start_char 不再都定位到首次出现 (顺序游标)。"""
        para = "这是一段会重复出现的内容用于触发问题。"  # 单段, 无换行
        content = "\n\n".join([para, "中间夹一段不同的内容。", para])
        # min_chunk_size_tokens=1 防短块合并, 让两个 para 各自成块
        chunks = chunk_document("doc1", content, "f.md", chunk_size_tokens=5, min_chunk_size_tokens=1)

        para_chunks = [c for c in chunks if c["content"] == normalize_text(para)]
        self.assertEqual(len(para_chunks), 2)  # para 出现两次, 各成一块
        s0 = para_chunks[0]["meta"]["start_char"]
        s1 = para_chunks[1]["meta"]["start_char"]
        # 旧实现 content.find(para) 两块都拿到首次出现 → s0 == s1; 修后第二块定位到真实(更后)位置
        self.assertLess(s0, s1)

    def test_offsets_are_valid_ranges(self):
        """所有 chunk 的 start/end_char 落在 normalize 后内容范围内且 start<=end (不变量)。"""
        content = "段落一。\n\n\n\n段落二有点长" + "啊" * 50 + "。\n\n段落三。"
        norm = normalize_text(content)
        chunks = chunk_document("doc2", content, "f.md", chunk_size_tokens=8, min_chunk_size_tokens=1)
        last_start = -1
        for c in chunks:
            s, e = c["meta"]["start_char"], c["meta"]["end_char"]
            self.assertTrue(0 <= s <= e <= len(norm), f"bad offset {s},{e} vs {len(norm)}")
            self.assertGreaterEqual(s, last_start)  # start_char 单调非递减
            last_start = s

    def test_joined_buffer_span_points_to_real_source(self):
        """回归(F1/F3): 多句单段, 缓冲块用 \\n\\n 重拼后无法在源文 verbatim find,
        旧实现要么 find 失败回退到 start=0 且 end=start+len(content) 越界,
        要么把 offset 指向错误源文。修后整块 span 由"真实单元"定位 ——
        offset 落在源文真实区间内、不越界、且第一/最后单元确实在 [s,e) 内。
        """
        rep = "重复句子在此处出现。"  # 同一句重复, 触发"定位到错误出现位置"风险
        # 单段(无空行) + 句间用空格 => 走句子分支; 源文分隔符是空格, 与缓冲的 \n\n 不同
        content = "起始句子在这里。 " + rep + " 中间句子不同。 " + rep + " 结尾句子收尾。"
        norm = normalize_text(content)
        chunks = chunk_document("docJ", content, "f.txt", chunk_size_tokens=8, min_chunk_size_tokens=1)
        self.assertGreater(len(chunks), 0)
        last_start = -1
        for c in chunks:
            s, e = c["meta"]["start_char"], c["meta"]["end_char"]
            self.assertTrue(0 <= s <= e <= len(norm), f"bad offset {s},{e} vs {len(norm)}")
            self.assertGreaterEqual(s, last_start)  # 单调非递减
            last_start = s
            # 拆出本块包含的单元(以 \n\n 重拼), 首单元应出现在 span 起点, 末单元应在 span 内结束
            units = [u for u in c["content"].split("\n\n") if u]
            self.assertTrue(norm[s:e].startswith(units[0]),
                            f"span 起点未对齐首单元: src={norm[s:e]!r} unit0={units[0]!r}")
            self.assertIn(units[-1], norm[s:e],
                          f"末单元不在 span 内: src={norm[s:e]!r} last={units[-1]!r}")

    def test_short_unit_merge_extends_end_to_real_source(self):
        """回归(F2): 过短单元合并到上一块时, end_char 必须扩到合并单元在源文的真实结束位置,
        而不是 start + len(merged_content) (合并 content 含人造分隔符, 算术 end 会越界/错位)。
        """
        big = "这是一段足够长的主体内容用于独立成块不会被合并掉哦。"
        tail = "短尾。"  # 过短, 会合并进上一块
        content = "\n\n".join([big, tail])
        norm = normalize_text(content)
        # min_chunk_size_tokens 设高让 tail 触发合并; big 单独 <= chunk_size 先成块
        chunks = chunk_document("docM", content, "f.txt", chunk_size_tokens=40, min_chunk_size_tokens=4)
        self.assertEqual(len(chunks), 1)  # tail 合并进 big, 只剩一块
        meta = chunks[0]["meta"]
        s, e = meta["start_char"], meta["end_char"]
        self.assertTrue(0 <= s <= e <= len(norm), f"merge offset 越界: {s},{e} vs {len(norm)}")
        # end_char 应覆盖到 tail 在源文的真实结束 (= 整段末尾), 而非算术 start+len(content)
        self.assertIn(tail, norm[s:e], "合并块 span 未覆盖到被合并单元的真实源文位置")
        self.assertEqual(e, len(norm))  # 本例 tail 是源文末尾


if __name__ == "__main__":
    unittest.main()
