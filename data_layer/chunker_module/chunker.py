# -*- coding: utf-8 -*-
"""
最小可运行 Chunking 工具(实现文档第 11 章规范)

适用范围:
    索引构建主链: parse -> store -> chunk -> embed -> upsert

切分策略(见文档 11.3 节):
    1. Markdown 标题(# / ## / ### ...)优先
    2. 段落空行次之
    3. 句号/分号/换行兜底
    4. 仍超 max_chunk_size_tokens 时,降级为滑动窗口

每个 chunk 的强制字段(见文档 11.1 节):
    doc_id / chunk_id / content / meta.{file_name, source, chunk_index,
    start_char, end_char, token_count_est}
"""

from __future__ import annotations

import math
import re
from typing import Dict, Any, List


def estimate_tokens(text: str) -> int:
    """简单估算 token 数: 字符数 / 4(英文/中文都近似适用)。

    生产建议替换为 tokenizer 精确计数(见文档 11.1 节)。
    """
    if not text:
        return 1
    return max(1, math.ceil(len(text) / 4))


def normalize_text(text: str) -> str:
    """统一换行符、压缩多余空行、去首尾空白。"""
    if text is None:
        return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_by_natural_boundaries(text: str) -> List[str]:
    """按自然边界拆文本: Markdown 标题 > 段落 > 句号。"""
    text = normalize_text(text)
    if not text:
        return []

    # 1. Markdown 标题分块
    if re.search(r"(?m)^#{1,6}\s+", text):
        sections = re.split(r"(?m)(?=^#{1,6}\s+)", text)
        parts = [s.strip() for s in sections if s.strip()]
        if parts:
            return parts

    # 2. 段落空行
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) > 1:
        return paras

    # 3. 句号/分号/换行
    sents = re.split(r"(?<=[。！？.!?；;])\s+|\n", text)
    sents = [s.strip() for s in sents if s.strip()]
    if sents:
        return sents

    return [text]


def chunk_document(
    doc_id: str,
    content: str,
    file_name: str,
    source: str = "local",
    chunk_size_tokens: int = 400,
    chunk_overlap_tokens: int = 80,
    max_chunk_size_tokens: int = 800,
    min_chunk_size_tokens: int = 80,
) -> List[Dict[str, Any]]:
    """主入口: 把整篇文档切成符合文档第 11 章规范的 chunk 列表。

    参数:
        doc_id: 文档唯一 ID(与 DocumentStore 一致)
        content: 原始文档内容
        file_name: 原文件名
        source: 来源标识(local/s3/oss/wiki/...)
        chunk_size_tokens: 目标 chunk 大小(默认 400, 推荐范围 300-600)
        chunk_overlap_tokens: 滑动窗口重叠 token 数(默认 80, 推荐 60-120)
        max_chunk_size_tokens: 单 chunk 硬上限(默认 800)
        min_chunk_size_tokens: 单 chunk 最小阈值(默认 80, 太小会合并到前一块)

    返回:
        chunk 列表,每项结构见模块 docstring
    """
    content = normalize_text(content)
    if not content:
        return []

    units = split_by_natural_boundaries(content)

    content_len = len(content)

    chunks: List[Dict[str, Any]] = []
    buffer_units: List[str] = []  # 当前缓冲块的"原始单元"序列 (用于按真实单元在源文定位 span)
    buffer_text = ""
    chunk_index = 0
    search_cursor = 0  # 顺序游标: 从上一块末尾往后找, 防重复段落 find 回到首次出现

    def _locate_span(piece_units: List[str]) -> tuple:
        """按"源文真实单元"定位整块的 (start_char, end_char)。

        chunk 的 content 是若干单元用 "\n\n" 重拼后再 normalize 的结果,
        这个拼接串通常无法在源文里 verbatim find 到 (源文分隔符可能是单个空格/换行,
        且短块合并/滑窗会再改写 content)。因此不能用 content.find(whole_content) 或
        start + len(content) 算 end —— 那会让 offset 指向错误源文。

        做法: 顺着游标逐个把"原始单元"定位到源文 (单元本身一定是 normalize 后源文的
        verbatim 子串), 整块跨度 = 第一个单元 start .. 最后一个单元 end。命中才推进游标,
        保证 start_char 单调不回退; 任一单元定位失败则回退到安全区间 (单调、不越界)。
        """
        nonlocal search_cursor
        cursor = search_cursor
        first_start = None
        last_end = None
        for u in piece_units:
            u = normalize_text(u)
            if not u:
                continue
            pos = content.find(u, cursor)
            if pos < 0:
                # 该单元在游标之后无法 verbatim 定位 (重复内容耗尽 / 异常改写):
                # 不再硬编偏移指向错误源文, 退化为安全区间 (单调非递减且不越界)。
                fallback_start = chunks[-1]["meta"]["end_char"] if chunks else 0
                fallback_start = min(fallback_start, content_len)
                return fallback_start, content_len
            if first_start is None:
                first_start = pos
            last_end = pos + len(u)
            cursor = last_end
        if first_start is None:
            # piece 全空 (理论上不会到这里, flush_buffer 已挡空串)
            fallback_start = chunks[-1]["meta"]["end_char"] if chunks else 0
            fallback_start = min(fallback_start, content_len)
            return fallback_start, fallback_start
        search_cursor = last_end
        return first_start, last_end

    def flush_buffer(text_piece: str, piece_units: List[str]):
        nonlocal chunk_index
        text_piece = normalize_text(text_piece)
        if not text_piece:
            return

        token_count_est = estimate_tokens(text_piece)
        if token_count_est < min_chunk_size_tokens and chunks:
            # 过短 chunk 合并到上一块,避免噪声/误召回。
            # end_char 必须扩到合并单元在源文的真实结束位置, 不能用 start + len(content) 算 ——
            # 合并后的 content 含人造分隔符, 在源文里 find 不到, 算术 end 会指向错误源文。
            prev = chunks[-1]
            prev["content"] = normalize_text(prev["content"] + "\n" + text_piece)
            _, merged_end = _locate_span(piece_units)
            prev_end = prev["meta"]["end_char"]
            # 合并块结束位置取两者较大 (单调不回退), 且不越界
            prev["meta"]["end_char"] = min(max(prev_end, merged_end), content_len)
            prev["meta"]["token_count_est"] = estimate_tokens(prev["content"])
            return

        chunk_index += 1
        # 按真实单元定位整块 span: 重复段落不再都定位到首次出现, 且 offset 永远指向真实源文区间。
        # 注: offset 仍是相对 normalize 后的 content; 与 doc_store 原文的 normalize 差异是另一更深的
        # 课题 (需带偏移映射重做切分契约), 这里治"重复段落 find 回退 / 拼接串 find 失败越界 / 合并块
        # 算术 end 错位"三类对齐子问题。
        start_char, end_char = _locate_span(piece_units)

        chunk_id = f"{doc_id}#c{chunk_index:06d}"
        chunks.append(
            {
                "doc_id": doc_id,
                "chunk_id": chunk_id,
                "content": text_piece,
                "meta": {
                    "file_name": file_name,
                    "source": source,
                    "chunk_index": chunk_index,
                    "start_char": start_char,
                    "end_char": end_char,
                    "token_count_est": token_count_est,
                },
            }
        )

    for unit in units:
        candidate = normalize_text((buffer_text + "\n\n" + unit) if buffer_text else unit)
        candidate_tokens = estimate_tokens(candidate)

        if candidate_tokens <= chunk_size_tokens:
            buffer_text = candidate
            buffer_units.append(unit)
            continue

        if buffer_text:
            flush_buffer(buffer_text, buffer_units)
            buffer_units = []

        if estimate_tokens(unit) > max_chunk_size_tokens:
            # 单元过长,降级为滑动窗口。每个 piece 是 normalize 后单元的 verbatim 切片,
            # 本身就是源文子串, 直接作为自己的"单元"定位即可。
            approx_chars = chunk_size_tokens * 4
            overlap_chars = chunk_overlap_tokens * 4
            start = 0
            unit = normalize_text(unit)
            while start < len(unit):
                end = min(len(unit), start + approx_chars)
                piece = normalize_text(unit[start:end])
                flush_buffer(piece, [piece])
                if end >= len(unit):
                    break
                start = max(start + 1, end - overlap_chars)
            buffer_text = ""
            buffer_units = []
        else:
            buffer_text = unit
            buffer_units = [unit]

    if buffer_text:
        flush_buffer(buffer_text, buffer_units)
        buffer_units = []

    return chunks


def build_upsert_items(
    chunks: List[Dict[str, Any]],
    vectors: List[List[float]],
) -> List[Dict[str, Any]]:
    """把 chunks + vectors 组合成符合 BaseVectorDB.upsert_vectors 契约的 items。

    每个 item 的 metadata 强制包含 doc_id/chunk_id/file_name/chunk_index(见文档 11.4),
    并额外包含 start_char/end_char/source 以支持引用回溯与去重。

    P8: 不再把全文塞进 metadata (此前 meta 体积是向量本体的 2.5 倍, 同一内容
    多落盘一份)。检索命中后由 RAG 按 doc_id + start/end_char 从 document_store
    抠原文 (_try_resolve_content_from_doc_store), 旧索引 meta 仍带 content 时
    优先直用、互相兼容。
    """
    items = []
    for chunk, vec in zip(chunks, vectors):
        items.append(
            {
                "vector_id": chunk["chunk_id"],
                "embedding": vec,
                "metadata": {
                    "doc_id": chunk["doc_id"],
                    "chunk_id": chunk["chunk_id"],
                    "file_name": chunk["meta"]["file_name"],
                    "chunk_index": chunk["meta"]["chunk_index"],
                    "start_char": chunk["meta"]["start_char"],
                    "end_char": chunk["meta"]["end_char"],
                    "source": chunk["meta"]["source"],
                },
            }
        )
    return items
