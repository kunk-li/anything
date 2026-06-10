from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


REQUIRED_VECTOR_KEYS = ("vector_id", "embedding", "metadata")
REQUIRED_METADATA_KEYS = ("doc_id", "chunk_id")


def validate_vector_item(item: Dict[str, Any], dim: int) -> Tuple[str, np.ndarray, Dict[str, Any]]:
    """校验单条向量数据并返回标准化结果。

    返回：
        (vector_id, embedding_np_float32, metadata)
    """
    if not isinstance(item, dict):
        raise TypeError("vector item must be a dict")

    for k in REQUIRED_VECTOR_KEYS:
        if k not in item:
            raise ValueError(f"vector item missing required key: {k}")

    vector_id = item["vector_id"]
    if not isinstance(vector_id, str) or not vector_id.strip():
        raise ValueError("vector_id must be a non-empty str")

    embedding = item["embedding"]
    if not isinstance(embedding, (list, tuple)):
        raise TypeError("embedding must be list[float]")
    if len(embedding) != dim:
        raise ValueError(f"embedding dimension mismatch: expected {dim}, got {len(embedding)}")

    emb = np.asarray(embedding, dtype="float32")
    if np.isnan(emb).any() or np.isinf(emb).any():
        raise ValueError("embedding contains NaN/Inf")

    metadata = item["metadata"]
    if not isinstance(metadata, dict):
        raise TypeError("metadata must be dict")
    for mk in REQUIRED_METADATA_KEYS:
        if mk not in metadata:
            raise ValueError(f"metadata missing required key: {mk}")

    return vector_id, emb, metadata


def validate_vectors(vectors: List[Dict[str, Any]], dim: int) -> List[Tuple[str, np.ndarray, Dict[str, Any]]]:
    """批量校验向量列表。"""
    if not isinstance(vectors, list) or not vectors:
        raise ValueError("vectors must be a non-empty list")
    out: List[Tuple[str, np.ndarray, Dict[str, Any]]] = []
    for item in vectors:
        out.append(validate_vector_item(item, dim))
    return out


def normalize_embeddings(mat: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    """L2 归一化（用于将内积转为余弦相似度）。"""
    if mat.ndim == 1:
        mat = mat.reshape(1, -1)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.maximum(norms, eps)
    return mat / norms


def match_metadata_filter(metadata: Dict[str, Any], filter: Optional[Dict[str, Any]]) -> bool:
    """过滤匹配：filter 值为标量时等值比较; 为 list/set/tuple 时做集合成员判定
    (P15: KB 的 doc_id 集合下推到检索阶段过滤用)。"""
    if not filter:
        return True
    if not isinstance(filter, dict):
        raise TypeError("filter must be a dict")
    for k, v in filter.items():
        actual = metadata.get(k)
        if isinstance(v, (list, set, tuple, frozenset)):
            if actual not in v and str(actual) not in v:
                return False
        elif actual != v:
            return False
    return True
