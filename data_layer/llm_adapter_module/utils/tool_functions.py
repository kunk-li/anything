from __future__ import annotations

import base64
import os
import uuid
import time
import math
from typing import List, Optional, Tuple, Dict, Any

from llm_adapter_module.model.data_model import FileContent, MediaContent


def gen_trace_id() -> str:
    return uuid.uuid4().hex


def now_ts() -> float:
    return time.time()


def normalize_vector(vec: List[float]) -> List[float]:
    """L2 归一化，不改变维度"""
    norm = math.sqrt(sum((x or 0.0) * (x or 0.0) for x in vec))
    if norm <= 0:
        return vec
    return [(x / norm) for x in vec]


def split_text(text: str, max_len: int = 1500, overlap: int = 100) -> List[str]:
    """简单按长度分段（可替换为更高级的按句分段）"""
    if not text:
        return []
    if max_len <= 0:
        return [text]
    chunks: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        end = min(n, i + max_len)
        chunk = text[i:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == n:
            break
        i = max(0, end - overlap)
    return chunks


def ensure_file_content_splits(file_content: FileContent, max_len: int = 1500, overlap: int = 100) -> FileContent:
    if file_content.split_contents:
        return file_content
    if file_content.text_content:
        file_content.split_contents = split_text(file_content.text_content, max_len=max_len, overlap=overlap)
    else:
        file_content.split_contents = []
    return file_content


def file_to_base64(path: str, max_bytes: Optional[int] = None) -> str:
    with open(path, "rb") as f:
        data = f.read()
    if max_bytes is not None and len(data) > max_bytes:
        raise ValueError(f"media too large: {len(data)} bytes > {max_bytes}")
    return base64.b64encode(data).decode("utf-8")


def hydrate_media_base64(media: MediaContent, max_bytes: Optional[int] = None) -> MediaContent:
    """若未提供base64则从路径读取（适合小文件）"""
    if media.media_base64:
        return media
    if not media.media_path or not os.path.exists(media.media_path):
        return media
    media.media_base64 = file_to_base64(media.media_path, max_bytes=max_bytes)
    return media


def merge_media_from_file_and_request(file_media: Optional[List[MediaContent]], req_media: Optional[List[MediaContent]]) -> List[MediaContent]:
    merged: List[MediaContent] = []
    for lst in (file_media or [], req_media or []):
        for m in lst:
            merged.append(m)
    return merged


def safe_dict(obj: Any) -> Dict[str, Any]:
    """将 dataclass / 其他对象尽量转换为可序列化 dict，用于 request_info"""
    try:
        from dataclasses import asdict, is_dataclass
        if is_dataclass(obj):
            return asdict(obj)
    except Exception:
        pass
    if isinstance(obj, dict):
        return obj
    return {"value": str(obj)}
