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
        filter: Optional[Dict] = None,
    ) -> List[Dict]:
        """向量相似度检索。

        返回列表每个元素格式（强制）：
            {
                "vector_id": str,
                "score": float,
                "metadata": dict
            }
        说明：
        - score 为相似度得分（0~1，越高越相似）
        """

    @abstractmethod
    def delete(self, vector_ids: Optional[List[str]] = None, filter: Optional[Dict] = None) -> bool:
        """删除向量：支持按 vector_ids 批量删除，或按 filter 条件删除。

        返回：
            成功 True，失败 False（或抛出 VectorDBException）
        """
