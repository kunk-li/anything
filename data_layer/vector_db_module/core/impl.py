from __future__ import annotations

import json
import os
import threading
from typing import Dict, List, Optional

import numpy as np

from .base import BaseVectorDB
from ..utils.tool_functions import normalize_embeddings, validate_vectors, match_metadata_filter
from ..config.config import VectorDBConfig

# 依赖模块（基础支撑层）
# 注: logger 改由 deps 注入; SystemLogger 不再直接 import
from deps_module import BasicDeps

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
    """FAISS 本地向量库实现。

    - 索引：IndexIDMap2(IndexFlatIP)（需归一化，内积即余弦相似度；IDMap2 支持 remove_ids 真删除）
    - 持久化：faiss.index + embeddings.npy + meta.json，全部临时文件 + os.replace 原子替换
    - 更新策略：upsert 在内存中更新映射并重建索引（写放大问题留待存储架构轮优化）
    - 删除：delete(vector_ids/filters) 真删除 — 索引 remove_ids + 三个映射同步收缩
    - 并发：实例级 RLock，upsert/query/delete 互斥（上传索引跑在线程池里）
    - 迁移：旧格式（meta.json 无 int_of 字段 / IndexFlatIP 索引文件）启动时自动
      从 embeddings.npy 重建为新格式，数据不丢
    """

    def __init__(
        self,
        cfg: Optional[VectorDBConfig] = None,
        deps: Optional[BasicDeps] = None,
        tenant_id: str = "default",
    ):
        """
        Args:
            tenant_id: 租户标识 (Task #33). 数据按 tenant_id 切目录:
                <base_store_dir>/<tenant_id>/{faiss.index, meta.json, embeddings.npy}

                关键约定 (docs/multi-tenancy-design.md §10):
                - 写永远写到子目录 <base>/<tenant_id>/...
                - 读优先查子目录, 未找到时:
                  * 仅当 tenant_id == "default" 才 fallback 到老扁平路径 <base>/...
                  * 其他租户禁止 fallback (防越权)
                - 字符集白名单见 _validate_tenant_id (深度防御)
        """
        self.cfg = cfg or VectorDBConfig()
        self.tenant_id = self._validate_tenant_id(tenant_id)
        self.dim = int(self.cfg.get_vector_dimension())
        # base = yaml 配置的根目录; store_dir = base/<tenant_id>/ (实际工作目录)
        self.base_store_dir = self.cfg.get_local_store_dir()
        self.store_dir = os.path.join(self.base_store_dir, self.tenant_id)

        if not os.path.exists(self.store_dir):
            os.makedirs(self.store_dir, exist_ok=True)

        if faiss is None:
            raise VectorDBException("VECTOR_DB_CONNECT_FAILED", "未安装 faiss，请在 requirements 中添加 faiss-cpu")

        # 写路径: 子目录(永远)
        self.index_path = os.path.join(self.store_dir, "faiss.index")
        self.meta_path = os.path.join(self.store_dir, "meta.json")
        self.emb_path = os.path.join(self.store_dir, "embeddings.npy")

        # logger / config_manager 优先走 DI 注入;未注入时延迟构造一份(向后兼容)。
        # config_manager 用于 PR4b 配额查询 (quotas.<tid>.max_vector_store_mb)。
        if deps is not None:
            self.logger = deps.logger
            self.config_manager = deps.config
        else:
            from deps_module import build_basic_deps
            _deps = build_basic_deps()
            self.logger = _deps.logger
            self.config_manager = _deps.config

        # 内存数据结构
        self.id_map: List[str] = []
        self.meta_map: Dict[str, Dict] = {}
        self.embeddings: Optional[np.ndarray] = None  # shape [N, dim] float32, normalized
        # 真删除支撑: vector_id(str) <-> faiss int64 id 双向映射 + 单调计数器。
        # int id 一经分配终身不变 (删除不回收), int_of/next_int 随 meta.json 持久化。
        self._int_of: Dict[str, int] = {}
        self._vid_of: Dict[int, str] = {}
        self._next_int: int = 0

        # 上传索引在线程池里跑, 多请求并发 upsert/delete 必须互斥
        self._lock = threading.RLock()

        self.index = self._new_index()

        self._load_if_exists()

    def _new_index(self):
        """空索引: IndexIDMap2 包装 IndexFlatIP, 支持 add_with_ids / remove_ids。"""
        return faiss.IndexIDMap2(faiss.IndexFlatIP(self.dim))

    def _resolve_load_paths(self) -> tuple:
        """决定从哪三个文件路径加载 (Task #33 PR3b: 双重读 fallback).

        策略:
            1. 子目录 <base>/<tenant_id>/ 存在数据 -> 用子目录 (主路径)
            2. 子目录无数据 + tenant_id == "default" + 老扁平路径有数据 -> fallback
            3. 其他情况 -> 用子目录 (空,正常)

        非 default 租户禁止 fallback (防越权读到老扁平路径中其他租户数据).
        """
        if os.path.exists(self.meta_path):
            return self.meta_path, self.index_path, self.emb_path

        # 子目录无数据
        if self.tenant_id != "default":
            return self.meta_path, self.index_path, self.emb_path

        legacy_meta = os.path.join(self.base_store_dir, "meta.json")
        legacy_index = os.path.join(self.base_store_dir, "faiss.index")
        legacy_emb = os.path.join(self.base_store_dir, "embeddings.npy")
        if os.path.exists(legacy_meta):
            if self.logger:
                self.logger.info(
                    f"[migration] tenant=default 从老扁平路径 fallback 读取: {self.base_store_dir} "
                    "(运维迁移完成后, 新写入会到 <base>/default/, 此 fallback 自动消失)",
                    logger_name="vector_db_module",
                )
            return legacy_meta, legacy_index, legacy_emb

        return self.meta_path, self.index_path, self.emb_path

    # ------------------------- persistence -------------------------
    def _load_if_exists(self) -> None:
        """加载已存在的索引与元数据（若有）。

        旧格式迁移: meta.json 没有 int_of 字段 = IndexFlatIP 时代的数据 →
        忽略旧 index 文件, 按行序分配 int id 并从 embeddings.npy 重建 IDMap2,
        随后持久化为新格式 (一次性, 数据不丢)。
        """
        try:
            meta_path, index_path, emb_path = self._resolve_load_paths()

            legacy_format = False
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                self.id_map = list(meta.get("id_map", []))
                self.meta_map = dict(meta.get("meta_map", {}))
                if "int_of" in meta:
                    self._int_of = {str(k): int(v) for k, v in (meta.get("int_of") or {}).items()}
                    self._next_int = int(meta.get("next_int") or 0)
                else:
                    legacy_format = bool(self.id_map)

            if os.path.exists(emb_path):
                self.embeddings = np.load(emb_path).astype("float32", copy=False)
                # 防御：维度不符则忽略并重建为空
                if self.embeddings.ndim != 2 or self.embeddings.shape[1] != self.dim:
                    self.embeddings = None
                    self.id_map = []
                    self.meta_map = {}
                    self._int_of = {}
                    legacy_format = False

            if legacy_format:
                # 旧格式: 按行序分配 int id, 不读旧 IndexFlatIP 文件
                self._int_of = {vid: i for i, vid in enumerate(self.id_map)}
                self._next_int = len(self.id_map)
                self._rebuild_index()
                self._persist()
                if self.logger:
                    self.logger.info(
                        f"[migration] vector store 旧格式(IndexFlatIP) -> IndexIDMap2 完成: "
                        f"ntotal={self.index.ntotal}, store_dir={self.store_dir}",
                        logger_name="vector_db_module",
                    )
            elif os.path.exists(index_path):
                self.index = faiss.read_index(index_path)
            elif self.embeddings is not None and len(self.id_map) == self.embeddings.shape[0]:
                self._rebuild_index()

            # 校验 index / 映射 / int id 一致性, 不一致以 embeddings 为准重建
            if self.embeddings is None:
                if self.id_map or self.meta_map:
                    self.id_map = []
                    self.meta_map = {}
                    self._int_of = {}
                    self.index = self._new_index()
            else:
                if any(vid not in self._int_of for vid in self.id_map):
                    self._int_of = {vid: i for i, vid in enumerate(self.id_map)}
                    self._next_int = len(self.id_map)
                    self._rebuild_index()
                elif self.index.ntotal != self.embeddings.shape[0]:
                    self._rebuild_index()

            self._vid_of = {v: k for k, v in self._int_of.items()}
            if self._int_of:
                self._next_int = max(self._next_int, max(self._int_of.values()) + 1)

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
            self._int_of = {}
            self._vid_of = {}
            self._next_int = 0
            self.index = self._new_index()

    def _persist(self) -> None:
        """持久化当前索引和元数据到本地。

        原子写: 三个文件都先写 .tmp 再 os.replace — 进程中途崩溃不会留半截文件
        (半截文件会让下次 _load_if_exists 静默降级为空库, 等于全库丢失)。
        """
        try:
            # embeddings (tmp 名保持 .npy 后缀, np.save 才不会自行追加)
            emb_tmp = self.emb_path + ".tmp.npy"
            if self.embeddings is None:
                np.save(emb_tmp, np.zeros((0, self.dim), dtype="float32"))
            else:
                np.save(emb_tmp, self.embeddings.astype("float32", copy=False))
            os.replace(emb_tmp, self.emb_path)

            # meta
            meta = {
                "id_map": self.id_map,
                "meta_map": self.meta_map,
                "int_of": self._int_of,
                "next_int": self._next_int,
            }
            meta_tmp = self.meta_path + ".tmp"
            with open(meta_tmp, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            os.replace(meta_tmp, self.meta_path)

            # index
            index_tmp = self.index_path + ".tmp"
            faiss.write_index(self.index, index_tmp)
            os.replace(index_tmp, self.index_path)
        except Exception as e:
            if self.logger:
                self.logger.error(f"FaissVectorDB persist failed: {e}", logger_name="vector_db_module")
            raise VectorDBException("VECTOR_INSERT_FAILED", f"向量库持久化失败：{str(e)}")

    def _rebuild_index(self) -> None:
        """根据 embeddings + int id 映射重建 IndexIDMap2。"""
        self.index = self._new_index()
        if self.embeddings is not None and self.embeddings.shape[0] > 0:
            ids = np.asarray([self._int_of[vid] for vid in self.id_map], dtype="int64")
            self.index.add_with_ids(self.embeddings, ids)

    @staticmethod
    def _validate_tenant_id(tenant_id: str) -> str:
        """字符集白名单校验 (深度防御, 防 path traversal). 见 docs/multi-tenancy-design.md §9.2

        本期数据路径还没用 tenant_id (PR3a), 但接口先验证, 让恶意值在最早期暴露.
        """
        import re
        if not isinstance(tenant_id, str) or not re.match(r"^[a-z0-9_-]{3,32}$", tenant_id):
            raise VectorDBException(
                "PARAM_INVALID",
                f"tenant_id 必须是 3-32 位 [a-z0-9_-] 字符, 实际收到: {tenant_id!r}",
            )
        return tenant_id

    # ------------------------- interface -------------------------
    def _check_storage_quota(self, new_vector_count: int) -> None:
        """PR4b: 写入前查 quotas.<tid>.max_vector_store_mb 配额。

        预估写入后总大小 = (现有向量数 + 新增向量数) * dim * 4 bytes (float32),
        超过配置即抛 QUOTA_STORAGE_EXCEEDED (HTTP 429, retryable)。

        - 没配置 quota = 不限制 (向后兼容老部署)
        - 配额命中时 ERROR 级日志, 便于运维定位
        """
        try:
            key = f"quotas.{self.tenant_id}.max_vector_store_mb"
            limit_mb = self.config_manager.get_config(key, None)
        except Exception:
            limit_mb = None
        if limit_mb is None:
            return
        current_n = self.embeddings.shape[0] if self.embeddings is not None else 0
        projected_bytes = (current_n + max(new_vector_count, 0)) * self.dim * 4
        projected_mb = projected_bytes / (1024 * 1024)
        if projected_mb > float(limit_mb):
            if self.logger:
                self.logger.error(
                    f"[quota] vector storage quota exceeded: tenant={self.tenant_id} "
                    f"projected={projected_mb:.1f}MB limit={limit_mb}MB",
                    logger_name="vector_db_module",
                )
            raise VectorDBException(
                "QUOTA_STORAGE_EXCEEDED",
                f"向量存储超过租户配额: projected={projected_mb:.1f}MB, "
                f"limit={limit_mb}MB (tenant={self.tenant_id})",
            )

    def upsert_vectors(self, vectors: List[Dict]) -> bool:
        """写入/更新向量（重建索引；写放大优化留待存储架构轮）。"""
        try:
            with self._lock:
                # PR4b: 写前做配额硬限校验, 超限直接 429 不浪费 CPU 算 embedding
                self._check_storage_quota(len(vectors) if vectors else 0)
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

                # 新 vector_id 分配终身 int id (真删除用)
                for vid in appended:
                    if vid not in self._int_of:
                        self._int_of[vid] = self._next_int
                        self._vid_of[self._next_int] = vid
                        self._next_int += 1

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
            with self._lock:
                return self._query_locked(query_vector, top_k, filters)
        except VectorDBException:
            raise
        except Exception as e:
            if self.logger:
                self.logger.error(f"query failed: {e}", logger_name="vector_db_module")
            raise VectorDBException("VECTOR_QUERY_FAILED", f"向量检索失败：{str(e)}")

    def _query_locked(self, query_vector: List[float], top_k: int, filters: Optional[Dict]) -> List[Dict]:
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
                # IndexIDMap2 返回的是 add_with_ids 时给的 int64 id, 不是行号
                vid = self._vid_of.get(int(idx))
                if vid is None:
                    continue
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

    def delete(self, vector_ids: Optional[List[str]] = None, filters: Optional[Dict] = None) -> bool:
        """真删除向量: IndexIDMap2.remove_ids + 三个映射同步收缩 + 持久化。

        Args:
            vector_ids: 显式 vector_id 列表
            filters:    metadata 过滤 (如 {"doc_id": "xxx"}), 与 vector_ids 取并集

        返回: 删掉 ≥1 条返回 True; 无匹配返回 False (幂等, 不抛错)。
        """
        try:
            with self._lock:
                targets = set()
                for vid in (vector_ids or []):
                    if vid in self._int_of:
                        targets.add(vid)
                if filters:
                    for vid in self.id_map:
                        if match_metadata_filter(self.meta_map.get(vid, {}), filters):
                            targets.add(vid)
                if not targets:
                    return False

                ints = np.asarray([self._int_of[vid] for vid in targets], dtype="int64")
                self.index.remove_ids(ints)

                keep = [i for i, vid in enumerate(self.id_map) if vid not in targets]
                if self.embeddings is not None and len(self.id_map) == self.embeddings.shape[0]:
                    self.embeddings = (
                        self.embeddings[keep] if keep
                        else np.zeros((0, self.dim), dtype="float32")
                    )
                self.id_map = [self.id_map[i] for i in keep]
                for vid in targets:
                    self.meta_map.pop(vid, None)
                    int_id = self._int_of.pop(vid, None)
                    if int_id is not None:
                        self._vid_of.pop(int_id, None)

                self._persist()
                if self.logger:
                    self.logger.info(
                        f"delete success: removed={len(targets)}, ntotal={self.index.ntotal}",
                        logger_name="vector_db_module",
                    )
                return True
        except VectorDBException:
            raise
        except Exception as e:
            if self.logger:
                self.logger.error(f"delete failed: {e}", logger_name="vector_db_module")
            raise VectorDBException("VECTOR_DELETE_FAILED", f"向量删除失败：{str(e)}")

    def clear(self) -> bool:
        """清空整个租户向量库并持久化 (全量重建 /index/build 的第一步)。"""
        with self._lock:
            self.id_map = []
            self.meta_map = {}
            self.embeddings = None
            self._int_of = {}
            self._vid_of = {}
            self._next_int = 0
            self.index = self._new_index()
            self._persist()
            return True
