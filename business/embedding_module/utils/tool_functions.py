from __future__ import annotations

import math
import re
from typing import Iterable, List, Sequence


class EmbeddingValidationError(ValueError):
    """Embedding 参数/格式校验异常。"""


def clean_text(text: str) -> str:
    """基础文本清洗：压缩空白、去除首尾空格。"""
    if text is None:
        raise EmbeddingValidationError("text 不能为空")
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if not cleaned:
        raise EmbeddingValidationError("text 不能为空字符串")
    return cleaned


def clean_texts(texts: Sequence[str]) -> List[str]:
    """批量文本清洗。"""
    if texts is None:
        raise EmbeddingValidationError("texts 不能为空")
    if isinstance(texts, (str, bytes)):
        raise EmbeddingValidationError("texts 必须为字符串列表")
    cleaned = [clean_text(item) for item in texts]
    if not cleaned:
        raise EmbeddingValidationError("texts 不能为空列表")
    return cleaned


def normalize_vector(vector: Iterable[float]) -> List[float]:
    """向量归一化。"""
    values = [float(v) for v in vector]
    if not values:
        raise EmbeddingValidationError("vector 不能为空")
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0:
        return values
    return [v / norm for v in values]


def normalize_vectors(vectors: Sequence[Iterable[float]]) -> List[List[float]]:
    """批量向量归一化。"""
    return [normalize_vector(vector) for vector in vectors]


def validate_vector_dim(vector: Sequence[float], expected_dim: int) -> None:
    """校验单条向量维度。"""
    if expected_dim <= 0:
        return
    if len(vector) != expected_dim:
        raise EmbeddingValidationError(
            f"向量维度不匹配，expected={expected_dim}, actual={len(vector)}"
        )