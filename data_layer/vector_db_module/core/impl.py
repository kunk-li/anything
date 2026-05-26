from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import numpy as np

from .base import BaseVectorDB
from ..utils.tool_functions import normalize_embeddings, validate_vectors, match_metadata_filter
from ..config.config import VectorDBConfig

# 依赖模块（基础支撑层）
try:
    from log_module.core.impl import SystemLogger
except Exception:  # pragma: no cover
    SystemLogger = None  # type: ignore

try:
    from exception_module.core.impl import VectorDBException
except Exception:  # pragma: no cover
    class VectorDBException(Exception):  # type: ignore
        def __init__(self, code: str, message: str):
            self.code = code
            self.message = message
            super().__init__(message)

# 向量数据库依赖（FAISS 示例）
try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None


def get_vector_db() -> BaseVectorDB:
    """根据配置返回向量数据库实例（工厂方法）。

    约束：上层模块应依赖 BaseVectorDB / 此工厂方法，而非直接依赖具体实现类。
    """
    cfg = VectorDBConfig()
    db_type = (cfg.get_vector_db_type() or "faiss").lower()
    if db_type == "faiss":
        return FaissVectorDB(cfg=cfg)
    raise VectorDBException("VECTOR_DB_CONNECT_FAILED", f"不支持的向量数据库类型：{db_type}")


class FaissVectorDB(BaseVectorDB):
    """FAISS 本地向量库实现（示例）。

    - 索引：IndexFlatIP（需归一化，内积即余弦相似度）
    - 持久化：faiss.index + embeddings.npy + meta.json
    - 更新策略：IndexFlatIP 不支持原位更新，因此 upsert 会在内存中更新映射并重建索引
    """

    def __init__(self, cfg: Optional[VectorDBConfig] = None):
        self.cfg = cfg or VectorDBConfig()
        self.dim = int(self.cfg.get_vector_dimension())
        self.store_dir = self.cfg.get_local_store_dir()

        if not os.path.exists(self.store_dir):
            os.makedirs(self.store_dir, exist_ok=True)

        if faiss is None:
            raise VectorDBException("VECTOR_DB_CONNECT_FAILED", "未安装 faiss，请在 requirements 中添加 faiss-cpu")

        self.index_path = os.path.join(self.store_dir, "faiss.index")
        self.meta_path = os.path.join(self.store_dir, "meta.json")
        self.emb_path = os.path.join(self.store_dir, "embeddings.npy")

        self.logger = SystemLogger() if SystemLogger is not None else None

        # 内存数据结构
        self.id_map: List[str] = []
        self.meta_map: Dict[str, Dict] = {}
        self.embeddings: Optional[np.ndarray] = None  # shape [N, dim] float32, normalized

        self.index = faiss.IndexFlatIP(self.dim)

        self._load_if_exists()

    # ------------------------- persistence -------------------------
    def _load_if_exists(self) -> None:
        """加载已存在的索引与元数据（若有）。"""
        try:
            if os.path.exists(self.meta_path):
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                self.id_map = list(meta.get("id_map", []))
                self.meta_map = dict(meta.get("meta_map", {}))

            if os.path.exists(self.emb_path):
                self.embeddings = np.load(self.emb_path).astype("float32", copy=False)
                # 防御：维度不符则忽略并重建为空
                if self.embeddings.ndim != 2 or self.embeddings.shape[1] != self.dim:
                    self.embeddings = None
                    self.id_map = []
                    self.meta_map = {}

            # index 文件存在则加载；否则用 embeddings 重建
            if os.path.exists(self.index_path):
                self.index = faiss.read_index(self.index_path)
            elif self.embeddings is not None and len(self.id_map) == self.embeddings.shape[0]:
                self._rebuild_index()

            # 再次校验 index 与映射一致性
            if self.embeddings is None:
                if self.id_map or self.meta_map:
                    # 清理不一致状态
                    self.id_map = []
                    self.meta_map = {}
                    self.index = faiss.IndexFlatIP(self.dim)
            else:
                if self.index.ntotal != self.embeddings.shape[0]:
                    self._rebuild_index()

            if self.logger:
                self.logger.info(
                    f"FaissVectorDB loaded: ntotal={self.index.ntotal}, store_dir={self.store_dir}",
                    logger_name="vector_db_module",
                )
        except Exception as e:
            if self.logger:
                self.logger.error(f"FaissVectorDB load failed: {e}", logger_name="vector_db_module")
            # 遇到损坏文件时，降级为空库，避免系统无法启动
            self.id_map = []
            self.meta_map = {}
            self.embeddings = None
            self.index = faiss.IndexFlatIP(self.dim)

    def _persist(self) -> None:
        """持久化当前索引和元数据到本地。"""
        try:
            # embeddings
            if self.embeddings is None:
                # 保持文件一致性：写空数组
                empty = np.zeros((0, self.dim), dtype="float32")
                np.save(self.emb_path, empty)
            else:
                np.save(self.emb_path, self.embeddings.astype("float32", copy=False))

            # meta
            meta = {"id_map": self.id_map, "meta_map": self.meta_map}
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            # index
            faiss.write_index(self.index, self.index_path)
        except Exception as e:
            if self.logger:
                self.logger.error(f"FaissVectorDB persist failed: {e}", logger_name="vector_db_module")
            raise VectorDBException("VECTOR_INSERT_FAILED", f"向量库持久化失败：{str(e)}")

    def _rebuild_index(self) -> None:
        """根据 embeddings 重建 IndexFlatIP。"""
        self.index = faiss.IndexFlatIP(self.dim)
        if self.embeddings is not None and self.embeddings.shape[0] > 0:
            self.index.add(self.embeddings)

    # ------------------------- interface -------------------------
    def upsert_vectors(self, vectors: List[Dict]) -> bool:
        """写入/更新向量（示例实现：重建索引）。"""
        try:
            items = validate_vectors(vectors, self.dim)
            # 转为 dict 便于更新
            new_embs: Dict[str, np.ndarray] = {}
            new_meta: Dict[str, Dict] = {}
            for vector_id, emb, meta in items:
                new_embs[vector_id] = emb
                new_meta[vector_id] = meta

            # 初始化现有 embeddings 映射
            existing: Dict[str, np.ndarray] = {}
            if self.embeddings is not None and len(self.id_map) == self.embeddings.shape[0]:
                for i, vid in enumerate(self.id_map):
                    existing[vid] = self.embeddings[i]

            # upsert：更新/新增
            existing.update(new_embs)
            self.meta_map.update(new_meta)

            # 重新生成 id_map / embeddings（稳定顺序：先保留旧顺序，再追加新 id）
            old_ids = [vid for vid in self.id_map if vid in existing]
            appended = [vid for vid in existing.keys() if vid not in set(old_ids)]
            self.id_map = old_ids + appended

            mat = np.stack([existing[vid] for vid in self.id_map], axis=0).astype("float32", copy=False)
            mat = normalize_embeddings(mat)
            self.embeddings = mat

            self._rebuild_index()
            self._persist()

            if self.logger:
                self.logger.info(
                    f"upsert_vectors success: count={len(items)}, ntotal={self.index.ntotal}",
                    logger_name="vector_db_module",
                )
            return True
        except VectorDBException:
            raise
        except Exception as e:
            if self.logger:
                self.logger.error(f"upsert_vectors failed: {e}", logger_name="vector_db_module")
            raise VectorDBException("VECTOR_INSERT_FAILED", f"向量插入/更新失败：{str(e)}")

    def query(self, query_vector: List[float], top_k: int = 5, filters: Optional[Dict] = None) -> List[Dict]:
        """向量相似度检索。"""
        try:
            if top_k <= 0:
                return []
            if self.index.ntotal == 0 or self.embeddings is None or not self.id_map:
                return []

            q = np.asarray(query_vector, dtype="float32")
            if q.shape[0] != self.dim:
                raise ValueError(f"query vector dimension mismatch: expected {self.dim}, got {q.shape[0]}")
            q = normalize_embeddings(q).astype("float32", copy=False)

            # 为支持 filters，先多取一些候选再过滤
            oversample = max(top_k * 10, top_k)
            oversample = min(oversample, int(self.index.ntotal))
            scores, idxs = self.index.search(q.reshape(1, -1), oversample)
            scores = scores.reshape(-1).tolist()
            idxs = idxs.reshape(-1).tolist()

            results: List[Dict] = []
            for score, idx in zip(scores, idxs):
                if idx < 0:
                    continue
                vid = self.id_map[idx]
                meta = self.meta_map.get(vid, {})
                if not match_metadata_filter(meta, filters):
                    continue
                # IndexFlatIP + 归一化 => 余弦相似度范围 [-1,1]，此处按文档约定映射到 [0,1]
                cos = float(score)
                norm_score = (cos + 1.0) / 2.0
                results.append({"vector_id": vid, "score": norm_score, "metadata": meta})
                if len(results) >= top_k:
                    break

            if self.logger:
                self.logger.debug(
                    f"query: top_k={top_k}, filters={filters}, returned={len(results)}",
                    logger_name="vector_db_module",
                )
            return results
        except VectorDBException:
            raise
        except Exception as e:
            if self.logger:
                self.logger.error(f"query failed: {e}", logger_name="vector_db_module")
            raise VectorDBException("VECTOR_QUERY_FAILED", f"向量检索失败：{str(e)}")

    def delete(self, vector_ids: Optional[List[str]] = None, filters: Optional[Dict] = None) -> bool:
        """删除向量。

        说明：
        - 设计文档明确：示例 FaissVectorDB 不支持 delete（IndexFlat* 不支持删除）
        - 生产环境可替换为支持删除的索引结构（如 IndexIDMap + remove_ids）或外部向量库
        """
        raise VectorDBException(
            "VECTOR_DELETE_NOT_SUPPORTED",
            "示例 FaissVectorDB 不支持删除，请在生产实现中使用可删除索引或外部向量库",
        )
