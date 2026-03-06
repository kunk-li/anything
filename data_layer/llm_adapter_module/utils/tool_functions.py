from __future__ import annotations

import base64
import os
import math
from typing import List, Optional, Dict, Any, Tuple

from llm_adapter_module.model.data_model import FileContent, MediaContent

# ----------------------------- text utils -----------------------------

def split_text(text: str, max_chars: int = 2000, overlap: int = 200) -> List[str]:
    """简单按字符切分文本，带 overlap，适配向量化/大模型上下文。"""
    if not text:
        return []
    max_chars = max(1, int(max_chars))
    overlap = max(0, int(overlap))
    if overlap >= max_chars:
        overlap = max_chars // 5
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + max_chars)
        chunks.append(text[start:end])
        if end == n:
            break
        start = end - overlap
    return [c for c in chunks if c.strip()]

def ensure_filecontent_splits(file_content: FileContent, max_chars: int = 2000, overlap: int = 200) -> FileContent:
    if file_content is None:
        return file_content
    if file_content.split_contents:
        return file_content
    if file_content.text_content:
        file_content.split_contents = split_text(file_content.text_content, max_chars=max_chars, overlap=overlap)
    return file_content

# ----------------------------- vector utils -----------------------------

def l2_normalize(vec: List[float]) -> List[float]:
    if not vec:
        return vec
    s = 0.0
    for x in vec:
        s += float(x) * float(x)
    if s <= 0:
        return vec
    norm = math.sqrt(s)
    return [float(x) / norm for x in vec]

def normalize_vectors(vectors: List[List[float]]) -> List[List[float]]:
    return [l2_normalize(v) for v in vectors]

# ----------------------------- media utils -----------------------------

def read_file_as_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def guess_mime(media_path: str) -> str:
    lower = media_path.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".mp3"):
        return "audio/mpeg"
    if lower.endswith(".wav"):
        return "audio/wav"
    if lower.endswith(".mp4"):
        return "video/mp4"
    return "application/octet-stream"

def validate_media_list(media_list: List[MediaContent], support_media: Optional[List[str]] = None, max_media_size_mb: Optional[int] = None) -> Tuple[bool, str]:
    support_media = support_media or []
    for m in media_list or []:
        if support_media and m.media_type not in support_media:
            return False, f"媒体类型不支持：{m.media_type}，支持：{support_media}"
        # size check
        if max_media_size_mb is not None and m.media_path and os.path.exists(m.media_path):
            size = os.path.getsize(m.media_path)
            if size > max_media_size_mb * 1024 * 1024:
                return False, f"媒体文件过大：{m.media_path}，大小{size}字节，限制{max_media_size_mb}MB"
    return True, ""

def to_openai_image_part(media: MediaContent) -> Dict[str, Any]:
    """将 MediaContent 转为 OpenAI chat/completions 的 image part（data url 形式）"""
    if media.media_type != "image":
        raise ValueError("to_openai_image_part only supports image")
    b64 = media.media_base64 or read_file_as_base64(media.media_path)
    mime = guess_mime(media.media_path)
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}"},
    }
