from __future__ import annotations

import json
import os
import sqlite3
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

    存储架构 (P7 消写放大):
    - 检索：内存 IndexIDMap2(IndexFlatIP)（需归一化，内积即余弦；支持 remove_ids 真删除）
    - 持久化：单一 vectors.sqlite3（行 = chunk_id/int_id/embedding blob/metadata json）
      * upsert/delete = 增量行级事务，O(本批) — 不再每次上传全量重建+重写三件套
      * 原子性由 sqlite 事务保证（WAL），不再需要 tmp+replace
      * 索引不落盘：启动时从 sqlite 扫描一次性 add_with_ids 重建（100k×384 维 = 秒级）
    - 内存只保留一份向量（在 faiss index 内部），不再持有 embeddings 矩阵副本
    - 并发：实例级 RLock，upsert/query/delete 互斥
    - 迁移：旧三件套 (faiss.index + meta.json + embeddings.npy，含更早的无 int_of 格式)
      在 sqlite 不存在时自动导入，旧文件保留原地作回滚备份（sqlite 存在即不再读）
    - filters: 等值匹配 + 值为 list/set 时做集合成员判定 (P15 KB 下推用)；
      filters 命中不足 top_k 时自动放大 oversample 重查至全库，杜绝"小子集召回饥饿"
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS vectors (
        chunk_id  TEXT PRIMARY KEY,
        int_id    INTEGER UNIQUE NOT NULL,
        embedding BLOB NOT NULL,
        metadata  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS kv (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
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
                <base_store_dir>/<tenant_id>/vectors.sqlite3

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

        # 主存储: sqlite 单库; 旧三件套路径仅迁移用 (legacy)
        self.db_path = os.path.join(self.store_dir, "vectors.sqlite3")
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

        # 内存数据结构 (向量本体只在 faiss index 里, 不再持矩阵副本)
        self.meta_map: Dict[str, Dict] = {}
        self._int_of: Dict[str, int] = {}
        self._vid_of: Dict[int, str] = {}
        self._next_int: int = 0

        # 上传索引在线程池里跑, 多请求并发 upsert/query/delete 必须互斥
        self._lock = threading.RLock()

        self.index = self._new_index()

        self._conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(self._SCHEMA)

        self._load()

    def _new_index(self):
        """空索引: IndexIDMap2 包装 IndexFlatIP, 支持 add_with_ids / remove_ids。"""
        return faiss.IndexIDMap2(faiss.IndexFlatIP(self.dim))

    # ------------------------- load / migration -------------------------
    def _load(self) -> None:
        """启动加载: sqlite 有数据则从 sqlite 重建内存索引;
        否则探测旧三件套 (含老扁平路径 fallback) 自动迁移。"""
        try:
            # 迁移开关用 kv 标记而非行数: 清空过的库 (0 行) 不能再回读旧三件套,
            # 否则 clear 后重启会"复活"已删数据
            initialized = self._conn.execute(
                "SELECT value FROM kv WHERE key='initialized'"
            ).fetchone()
            if initialized:
                self._load_from_sqlite()
            else:
                self._migrate_legacy_if_exists()
                with self._conn:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO kv (key, value) VALUES ('initialized', '1')"
                    )
            if self.logger:
                self.logger.info(
                    f"FaissVectorDB loaded: ntotal={self.index.ntotal}, store_dir={self.store_dir}",
                    logger_name="vector_db_module",
                )
        except Exception as e:
            if self.logger:
                self.logger.error(f"FaissVectorDB load failed: {e}", logger_name="vector_db_module")
            # 遇到损坏数据时，降级为空库，避免系统无法启动
            self.meta_map = {}
            self._int_of = {}
            self._vid_of = {}
            self._next_int = 0
            self.index = self._new_index()

    def _load_from_sqlite(self) -> None:
        rows = self._conn.execute(
            "SELECT chunk_id, int_id, embedding, metadata FROM vectors"
        ).fetchall()
        vids: List[str] = []
        ints: List[int] = []
        embs: List[np.ndarray] = []
        for chunk_id, int_id, blob, meta_json in rows:
            vec = np.frombuffer(blob, dtype="float32")
            if vec.shape[0] != self.dim:
                continue  # 维度不符的脏行跳过
            vids.append(chunk_id)
            ints.append(int(int_id))
            embs.append(vec)
            try:
                self.meta_map[chunk_id] = json.loads(meta_json)
            except (TypeError, ValueError):
                self.meta_map[chunk_id] = {}
        self._int_of = dict(zip(vids, ints))
        self._vid_of = dict(zip(ints, vids))
        row = self._conn.execute("SELECT value FROM kv WHERE key='next_int'").fetchone()
        self._next_int = int(row[0]) if row else (max(ints) + 1 if ints else 0)
        if embs:
            mat = np.stack(embs, axis=0).astype("float32", copy=False)
            self.index.add_with_ids(mat, np.asarray(ints, dtype="int64"))

    def _resolve_legacy_paths(self) -> tuple:
        """旧三件套从哪读 (Task #33 PR3b: 双重读 fallback, 仅迁移期用)。

        子目录有 meta.json 用子目录; 否则仅 default 租户允许 fallback 老扁平路径
        (防越权读到其他租户数据)。
        """
        if os.path.exists(self.meta_path):
            return self.meta_path, self.emb_path
        if self.tenant_id != "default":
            return self.meta_path, self.emb_path
        legacy_meta = os.path.join(self.base_store_dir, "meta.json")
        legacy_emb = os.path.join(self.base_store_dir, "embeddings.npy")
        if os.path.exists(legacy_meta):
            return legacy_meta, legacy_emb
        return self.meta_path, self.emb_path

    def _migrate_legacy_if_exists(self) -> None:
        """旧三件套 -> sqlite 一次性迁移。兼容两代旧格式:
        gen1: meta.json 只有 id_map/meta_map (IndexFlatIP 时代)
        gen2: meta.json 带 int_of/next_int (IndexIDMap2 三件套时代)
        旧文件迁移后保留原地作回滚备份 (sqlite 非空即不再读)。"""
        meta_path, emb_path = self._resolve_legacy_paths()
        if not (os.path.exists(meta_path) and os.path.exists(emb_path)):
            return
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        id_map = list(meta.get("id_map", []))
        meta_map = dict(meta.get("meta_map", {}))
        embeddings = np.load(emb_path).astype("float32", copy=False)
        if embeddings.ndim != 2 or embeddings.shape[1] != self.dim:
            if self.logger:
                self.logger.warning(
                    f"[migration] 旧 embeddings 维度不符 (got {embeddings.shape}), 跳过迁移",
                    logger_name="vector_db_module",
                )
            return
        if len(id_map) != embeddings.shape[0]:
            if self.logger:
                self.logger.warning(
                    "[migration] 旧 id_map 与 embeddings 行数不一致, 跳过迁移",
                    logger_name="vector_db_module",
                )
            return
        int_of_raw = meta.get("int_of")
        if isinstance(int_of_raw, dict) and int_of_raw:
            int_of = {str(k): int(v) for k, v in int_of_raw.items()}
            next_int = int(meta.get("next_int") or 0)
            if any(vid not in int_of for vid in id_map):
                int_of = {vid: i for i, vid in enumerate(id_map)}
                next_int = len(id_map)
        else:
            int_of = {vid: i for i, vid in enumerate(id_map)}
            next_int = len(id_map)
        next_int = max(next_int, (max(int_of.values()) + 1) if int_of else 0)

        mat = normalize_embeddings(embeddings)
        with self._conn:  # 单事务批量导入
            self._conn.executemany(
                "INSERT OR REPLACE INTO vectors (chunk_id, int_id, embedding, metadata) "
                "VALUES (?, ?, ?, ?)",
                [
                    (
                        vid,
                        int_of[vid],
                        mat[i].astype("float32").tobytes(),
                        json.dumps(meta_map.get(vid, {}), ensure_ascii=False),
                    )
                    for i, vid in enumerate(id_map)
                ],
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO kv (key, value) VALUES ('next_int', ?)",
                (str(next_int),),
            )
        self.meta_map = {vid: meta_map.get(vid, {}) for vid in id_map}
        self._int_of = int_of
        self._vid_of = {v: k for k, v in int_of.items()}
        self._next_int = next_int
        self.index.add_with_ids(
            mat, np.asarray([int_of[vid] for vid in id_map], dtype="int64")
        )
        if self.logger:
            self.logger.info(
                f"[migration] vector store 旧三件套 -> vectors.sqlite3 完成: "
                f"ntotal={self.index.ntotal}, store_dir={self.store_dir} "
                f"(旧文件保留原地作回滚备份)",
                logger_name="vector_db_module",
            )

    @staticmethod
    def _validate_tenant_id(tenant_id: str) -> str:
        """字符集白名单校验 (深度防御, 防 path traversal). 见 docs/multi-tenancy-design.md §9.2"""
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
        current_n = int(self.index.ntotal)
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
        """写入/更新向量 — 增量: 索引 remove+add 本批, sqlite 行级事务, O(本批)。"""
        try:
            with self._lock:
                # PR4b: 写前做配额硬限校验, 超限直接 429 不浪费 CPU 算 embedding
                self._check_storage_quota(len(vectors) if vectors else 0)
                items = validate_vectors(vectors, self.dim)
                if not items:
                    return True

                # 关键: 先把派生值算进局部变量, sqlite 持久化成功后再动内存
                # (index/_int_of/_vid_of/_next_int/meta_map)。
                # 否则 sqlite 提交失败时内存已被改, 与磁盘分叉 (data-loss):
                # 新向量进了索引却没落盘, 重载即丢; 更新的 id 索引是新值磁盘是旧值。
                # int_id 分配只算进局部 _int_of 视图, 实例状态暂不变更。
                int_of_view = dict(self._int_of)  # 仅供本批查询/分配, 不写回实例
                new_ids: Dict[str, int] = {}      # 本批新分配的 id (落盘后并入实例映射)
                vids: List[str] = []
                ints: List[int] = []
                embs: List[np.ndarray] = []
                metas: List[Dict] = []
                next_int = self._next_int
                for vector_id, emb, meta in items:
                    if vector_id not in int_of_view:
                        int_of_view[vector_id] = next_int
                        new_ids[vector_id] = next_int
                        next_int += 1
                    vids.append(vector_id)
                    ints.append(int_of_view[vector_id])
                    embs.append(emb)
                    metas.append(meta)

                mat = normalize_embeddings(
                    np.stack(embs, axis=0).astype("float32", copy=False)
                )

                # 已存在的 id: upsert 即覆盖, 同 int id 需先 remove 再 add
                updated_ints = [
                    self._int_of[vid] for vid in vids if vid in self._int_of
                ]

                # 1) 先持久化到 sqlite (失败则内存原封不动, 不分叉)
                with self._conn:
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO vectors (chunk_id, int_id, embedding, metadata) "
                        "VALUES (?, ?, ?, ?)",
                        [
                            (
                                vid,
                                ints[i],
                                mat[i].astype("float32").tobytes(),
                                json.dumps(metas[i], ensure_ascii=False),
                            )
                            for i, vid in enumerate(vids)
                        ],
                    )
                    self._conn.execute(
                        "INSERT OR REPLACE INTO kv (key, value) VALUES ('next_int', ?)",
                        (str(next_int),),
                    )

                # 2) sqlite 已 commit, 再改内存索引与映射 (此后才与磁盘一致);
                #    映射增量更新, 维持 O(本批) 而非全量重建。
                if updated_ints:
                    self.index.remove_ids(np.asarray(updated_ints, dtype="int64"))
                self.index.add_with_ids(mat, np.asarray(ints, dtype="int64"))
                for vid, iv in new_ids.items():
                    self._int_of[vid] = iv
                    self._vid_of[iv] = vid
                self._next_int = next_int
                for i, vid in enumerate(vids):
                    self.meta_map[vid] = metas[i]

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
        """向量相似度检索。

        filters 值支持标量等值或 list/set 集合成员判定 (P15 KB 下推);
        带 filters 时命中不足 top_k 会自动放大 oversample 重查 (最终到全库),
        避免"全局 top 候选全被滤掉 → 0 结果"的召回饥饿。
        """
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
        ntotal = int(self.index.ntotal)
        if ntotal == 0:
            return []

        q = np.asarray(query_vector, dtype="float32")
        if q.shape[0] != self.dim:
            raise ValueError(f"query vector dimension mismatch: expected {self.dim}, got {q.shape[0]}")
        q = normalize_embeddings(q).astype("float32", copy=False).reshape(1, -1)

        # filters=None 时收集满 top_k 即 break 且永不重查, 无需放大 10 倍多搜(纯算力浪费);
        # 仅带过滤时才 oversample 留出被过滤掉的余量。
        oversample = min(top_k, ntotal) if not filters else min(max(top_k * 10, top_k), ntotal)
        while True:
            scores, idxs = self.index.search(q, oversample)
            results: List[Dict] = []
            for score, idx in zip(scores.reshape(-1).tolist(), idxs.reshape(-1).tolist()):
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
            # filters 命中不足且还有未扫到的库存 → 放大候选重查
            if filters and len(results) < top_k and oversample < ntotal:
                oversample = min(oversample * 5, ntotal)
                continue
            break

        if self.logger:
            self.logger.debug(
                f"query: top_k={top_k}, filters={filters}, returned={len(results)}",
                logger_name="vector_db_module",
            )
        return results

    def delete(self, vector_ids: Optional[List[str]] = None, filters: Optional[Dict] = None) -> bool:
        """真删除向量: 索引 remove_ids + sqlite 行删除 + 映射收缩, O(删除数)。

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
                    for vid, meta in self.meta_map.items():
                        if match_metadata_filter(meta or {}, filters):
                            targets.add(vid)
                if not targets:
                    return False

                ints = np.asarray([self._int_of[vid] for vid in targets], dtype="int64")
                self.index.remove_ids(ints)
                with self._conn:
                    self._conn.executemany(
                        "DELETE FROM vectors WHERE chunk_id = ?",
                        [(vid,) for vid in targets],
                    )
                for vid in targets:
                    self.meta_map.pop(vid, None)
                    int_id = self._int_of.pop(vid, None)
                    if int_id is not None:
                        self._vid_of.pop(int_id, None)

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
        """清空整个租户向量库 (全量重建 /index/build 的第一步)。"""
        with self._lock:
            with self._conn:
                self._conn.execute("DELETE FROM vectors")
                self._conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('next_int', '0')")
            self.meta_map = {}
            self._int_of = {}
            self._vid_of = {}
            self._next_int = 0
            self.index = self._new_index()
            return True

    def close(self) -> None:
        """关闭 sqlite 连接 (主要给测试/优雅退出用)。"""
        try:
            self._conn.close()
        except Exception:
            pass
