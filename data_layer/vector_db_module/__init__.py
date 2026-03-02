"""vector_db_module

数据层 - 向量数据库模块：提供向量 upsert / query / delete 的统一接口与 FAISS 本地示例实现。

典型用法：
    from vector_db_module import get_vector_db
    db = get_vector_db()
    db.upsert_vectors([...])
    hits = db.query([...], top_k=5)
"""

from .core.base import BaseVectorDB
from .core.impl import FaissVectorDB, get_vector_db

__all__ = ["BaseVectorDB", "FaissVectorDB", "get_vector_db"]
