from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class BaseVectorDB(ABC):
    """向量数据库抽象基类。

    约束：
    - 具体实现类必须实现 upsert_vectors / query / delete
    - 返回值格式必须与此处定义一致，确保上层模块可替换
    """

    @abstractmethod
    def upsert_vectors(self, vectors: List[Dict]) -> bool:
        """写入/更新向量。

        参数 vectors 每个元素格式（强制）：
            {
                "vector_id": str,
                "embedding": List[float],
                "metadata": dict
            }
        说明：
        - vector_id 为向量唯一标识
        - embedding 为文本嵌入向量
        - metadata 至少包含 doc_id、chunk_id

        返回：
            成功 True，失败 False（或抛出 VectorDBException）
        """

    @abstractmethod
    def query(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filters: Optional[Dict] = None,
    ) -> List[Dict]:
        """向量相似度检索（强制签名，所有实现类必须严格匹配）。

        参数：
            query_vector: 查询向量（已归一化或由实现侧归一化）
            top_k: 返回结果上限
            filters: metadata 过滤条件，结构为 {"field": value} 或 {"field": {"$op": value}}

        返回列表每个元素格式（强制）：
            {
                "vector_id": str,
                "score": float,
                "metadata": dict
            }
        说明：
        - score 为相似度得分（0~1，越高越相似）
        - 上层模块禁止使用 embedding= / vector= 等别名调用，必须使用 query_vector
        """

    @abstractmethod
    def delete(self, vector_ids: Optional[List[str]] = None, filters: Optional[Dict] = None) -> bool:
        """删除向量：支持按 vector_ids 批量删除，或按 filters 条件删除。

        返回：
            成功 True，失败 False（或抛出 VectorDBException）
        """
