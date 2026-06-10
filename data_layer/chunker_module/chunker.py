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

    chunks: List[Dict[str, Any]] = []
    buffer_text = ""
    chunk_index = 0

    def flush_buffer(text_piece: str):
        nonlocal chunk_index
        text_piece = normalize_text(text_piece)
        if not text_piece:
            return

        token_count_est = estimate_tokens(text_piece)
        if token_count_est < min_chunk_size_tokens and chunks:
            # 过短 chunk 合并到上一块,避免噪声/误召回
            chunks[-1]["content"] = normalize_text(chunks[-1]["content"] + "\n" + text_piece)
            chunks[-1]["meta"]["end_char"] = chunks[-1]["meta"]["start_char"] + len(chunks[-1]["content"])
            chunks[-1]["meta"]["token_count_est"] = estimate_tokens(chunks[-1]["content"])
            return

        chunk_index += 1
        start_char = content.find(text_piece)
        if start_char < 0:
            start_char = 0 if not chunks else chunks[-1]["meta"]["end_char"]
        end_char = start_char + len(text_piece)

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
            continue

        if buffer_text:
            flush_buffer(buffer_text)

        if estimate_tokens(unit) > max_chunk_size_tokens:
            # 单元过长,降级为滑动窗口
            approx_chars = chunk_size_tokens * 4
            overlap_chars = chunk_overlap_tokens * 4
            start = 0
            unit = normalize_text(unit)
            while start < len(unit):
                end = min(len(unit), start + approx_chars)
                piece = normalize_text(unit[start:end])
                flush_buffer(piece)
                if end >= len(unit):
                    break
                start = max(start + 1, end - overlap_chars)
            buffer_text = ""
        else:
            buffer_text = unit

    if buffer_text:
        flush_buffer(buffer_text)

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
