# -*- coding: utf-8 -*-
"""
最小可运行 Chunking 工具
用于索引构建主链：parse -> store -> chunk -> embed -> upsert
"""

from __future__ import annotations

import math
import re
from typing import Dict, Any, List


def estimate_tokens(text: str) -> int:
    if not text:
        return 1
    return max(1, math.ceil(len(text) / 4))


def normalize_text(text: str) -> str:
    if text is None:
        return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_by_natural_boundaries(text: str) -> List[str]:
    text = normalize_text(text)
    if not text:
        return []

    if re.search(r"(?m)^#{1,6}\s+", text):
        sections = re.split(r"(?m)(?=^#{1,6}\s+)", text)
        parts = [s.strip() for s in sections if s.strip()]
        if parts:
            return parts

    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) > 1:
        return paras

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
                    "content": chunk["content"],
                },
            }
        )
    return items
