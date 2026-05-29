# -*- coding: utf-8 -*-
"""
LongTermMemoryImpl (Task DDD #90).

存储走 StateBackend (TT #80) — 默认 InMemoryBackend, 生产可换 SqliteBackend
让多 worker 进程共享同一份 fact 库, 容器重启不丢.

Key 结构 (StateBackend kv + list):
    memory:fact:<tenant>:<fact_id>     = JSON-encoded Fact            (kv)
    memory:hash:<tenant>:<hash>        = fact_id (反查, 实现 hash 去重)  (kv)
    memory:index:<tenant>              = list[fact_id]                (list, list_append)
    memory:tags:<tenant>:<tag>         = list[fact_id]                (list, tag 过滤用)

DDD MVP 阶段去重走 content_hash (sha1(content.lower().strip())). EEE #91 加
embedding 后, add_fact 时先按 hash 查; 若 hash 没命中, 再按 embedding cosine > 0.9
查近邻 (二级 dedup).

API 符合 BaseLongTermMemory ABC.
"""
from __future__ import annotations

import json
import time
from typing import List, Optional, TYPE_CHECKING

from long_term_memory_module.core.base import BaseLongTermMemory
from long_term_memory_module.model import Fact, MemoryHit, MemoryQuery

if TYPE_CHECKING:
    from state_backend_module import StateBackend


# Key 前缀常量 — 跟 backend.clear(prefix=) 配合
_KEY_PREFIX = "memory:"
_FACT_KEY = "memory:fact"
_HASH_KEY = "memory:hash"
_INDEX_KEY = "memory:index"
_TAG_KEY = "memory:tag"


class LongTermMemoryImpl(BaseLongTermMemory):
    """长期记忆默认实现, StateBackend 后端."""

    def __init__(self, backend: "StateBackend", max_facts_per_tenant: int = 10000):
        if backend is None:
            raise ValueError("LongTermMemoryImpl 需要 backend (StateBackend), 不能为 None")
        self._backend = backend
        self._max_facts_per_tenant = int(max_facts_per_tenant)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _fact_key(self, tenant_id: str, fact_id: str) -> str:
        return f"{_FACT_KEY}:{tenant_id}:{fact_id}"

    def _hash_key(self, tenant_id: str, content_hash: str) -> str:
        return f"{_HASH_KEY}:{tenant_id}:{content_hash}"

    def _index_key(self, tenant_id: str) -> str:
        return f"{_INDEX_KEY}:{tenant_id}"

    def _tag_index_key(self, tenant_id: str, tag: str) -> str:
        return f"{_TAG_KEY}:{tenant_id}:{tag}"

    def _load_fact(self, tenant_id: str, fact_id: str) -> Optional[Fact]:
        raw = self._backend.get(self._fact_key(tenant_id, fact_id))
        if raw is None:
            return None
        try:
            if isinstance(raw, str):
                raw = json.loads(raw)
            return Fact.model_validate(raw)
        except Exception:
            return None

    def _save_fact(self, fact: Fact) -> None:
        self._backend.set(
            self._fact_key(fact.tenant_id, fact.fact_id),
            fact.model_dump(mode="json"),
        )

    # ------------------------------------------------------------------
    # add_fact — 含 content_hash 去重 + tag 索引
    # ------------------------------------------------------------------

    def add_fact(self, fact: Fact) -> Fact:
        """新增 fact, 自动 content_hash 去重 (DDD MVP).
        命中已有 → 不重复存, 而是 mark_accessed 已有那条, 返回它.
        """
        fact = fact.ensure_hash()
        tenant = fact.tenant_id or "default"

        # 1. 按 content_hash 查重 — 用户设计的核心: "存在就标记"
        existing_id = self._backend.get(self._hash_key(tenant, fact.content_hash))
        if existing_id:
            existing = self._load_fact(tenant, str(existing_id))
            if existing is not None:
                # bump access_count + last_accessed, 不重复落盘新内容
                existing.access_count += 1
                existing.last_accessed = time.time()
                # tags 合并 (新 fact 带了之前没有的 tag, 也加上)
                for t in fact.tags:
                    if t not in existing.tags:
                        existing.tags.append(t)
                        # 同步加进 tag 索引
                        self._backend.list_append(
                            self._tag_index_key(tenant, t),
                            existing.fact_id,
                            maxlen=self._max_facts_per_tenant,
                        )
                self._save_fact(existing)
                return existing

        # 2. 新 fact 落盘
        self._save_fact(fact)
        # hash → fact_id 反查
        self._backend.set(self._hash_key(tenant, fact.content_hash), fact.fact_id)
        # tenant 索引
        self._backend.list_append(
            self._index_key(tenant),
            fact.fact_id,
            maxlen=self._max_facts_per_tenant,
        )
        # tag 索引
        for t in fact.tags:
            self._backend.list_append(
                self._tag_index_key(tenant, t),
                fact.fact_id,
                maxlen=self._max_facts_per_tenant,
            )
        return fact

    # ------------------------------------------------------------------
    # search_facts — DDD MVP 走子串匹配 (EEE 改 embedding cosine)
    # ------------------------------------------------------------------

    def search_facts(self, query: MemoryQuery) -> List[MemoryHit]:
        """DDD MVP 实现:
            1. 先按 content_hash 精确匹配 (cheap)
            2. fallback 子串包含匹配 (linear scan tenant 索引)
            3. 命中自动 mark_accessed (用户的 "标记" 设计)
        EEE #91 会替换成 embedding cosine.
        """
        tenant = query.tenant_id or "default"
        # 一阶段 — content_hash 精确命中 (query 与 fact 内容完全相同时极快)
        from long_term_memory_module.model.data_model import _content_hash
        exact_hash = _content_hash(query.query)
        exact_fact_id = self._backend.get(self._hash_key(tenant, exact_hash))
        hits: List[MemoryHit] = []
        seen_ids = set()
        if exact_fact_id:
            f = self._load_fact(tenant, str(exact_fact_id))
            if f and self._tag_filter_pass(f, query):
                hits.append(MemoryHit(fact=f, score=1.0, reason="content_hash_exact"))
                seen_ids.add(f.fact_id)
                self.mark_accessed(f.fact_id, tenant_id=tenant)

        # 二阶段 — substring 包含 (大小写不敏感) 兜底
        if len(hits) < query.top_k:
            q_lower = query.query.lower().strip()
            ids = self._backend.list_get(self._index_key(tenant))
            # 去重 (list_append 不去重)
            for fid in dict.fromkeys(ids):
                if fid in seen_ids:
                    continue
                f = self._load_fact(tenant, str(fid))
                if f is None:
                    continue
                if not self._tag_filter_pass(f, query):
                    continue
                if f.confidence < query.min_confidence:
                    continue
                if q_lower in f.content.lower():
                    # 简单 jaccard score: 重叠 chars / max(len)
                    score = min(0.95, len(q_lower) / max(len(f.content), 1))
                    hits.append(MemoryHit(fact=f, score=score, reason="substring"))
                    seen_ids.add(f.fact_id)
                    self.mark_accessed(f.fact_id, tenant_id=tenant)
                    if len(hits) >= query.top_k:
                        break

        # 按 score 倒序
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[: query.top_k]

    def _tag_filter_pass(self, fact: Fact, query: MemoryQuery) -> bool:
        if not query.tags_filter:
            return True
        return any(t in fact.tags for t in query.tags_filter)

    # ------------------------------------------------------------------
    # mark_accessed
    # ------------------------------------------------------------------

    def mark_accessed(self, fact_id: str, tenant_id: str = "default") -> bool:
        """access_count + 1, last_accessed = now. 返回 True 如果 fact 存在."""
        f = self._load_fact(tenant_id, fact_id)
        if f is None:
            return False
        f.access_count += 1
        f.last_accessed = time.time()
        self._save_fact(f)
        return True

    # ------------------------------------------------------------------
    # list_facts
    # ------------------------------------------------------------------

    def list_facts(
        self,
        tenant_id: str = "default",
        limit: int = 100,
        offset: int = 0,
        tags_filter: Optional[List[str]] = None,
    ) -> List[Fact]:
        """枚举 tenant 下 facts, 按 last_accessed 倒序."""
        ids = list(dict.fromkeys(self._backend.list_get(self._index_key(tenant_id))))
        out: List[Fact] = []
        for fid in ids:
            f = self._load_fact(tenant_id, str(fid))
            if f is None:
                continue
            if tags_filter and not any(t in f.tags for t in tags_filter):
                continue
            out.append(f)
        out.sort(key=lambda f: f.last_accessed, reverse=True)
        return out[offset : offset + limit]

    # ------------------------------------------------------------------
    # delete_fact
    # ------------------------------------------------------------------

    def delete_fact(self, fact_id: str) -> bool:
        """需要扫所有 tenant 查 fact_id 所在的 tenant. 大多数调用方
        (admin / agent) 都知道 tenant_id, 推荐用 delete_fact_in_tenant 那条路径.
        本方法兜底支持 fact_id-only 查询, 慢."""
        # 因为我们不知道 tenant_id, 退化扫所有 known tenants (一般规模 OK)
        # 实际上 self._backend 没有 "list keys with prefix" 操作, 这里只能
        # 通过 memory:tenants list 反推, 但我们没维护. 提供 delete_fact_in_tenant 给推荐路径.
        return False  # 兜底, 推荐用 delete_fact_in_tenant

    def delete_fact_in_tenant(self, fact_id: str, tenant_id: str = "default") -> bool:
        """删除指定 tenant 下的 fact. 同步清 hash 索引 + tag 索引."""
        f = self._load_fact(tenant_id, fact_id)
        if f is None:
            return False
        # 1. 删 fact body
        self._backend.set(self._fact_key(tenant_id, fact_id), None)
        # 2. 删 hash 反查 (只删指向此 fact 的那条)
        cur_hash_id = self._backend.get(self._hash_key(tenant_id, f.content_hash))
        if cur_hash_id == fact_id:
            self._backend.set(self._hash_key(tenant_id, f.content_hash), None)
        # 3. index list / tag list 暂不主动 prune (StateBackend 无 list_remove,
        #    list_get 时会通过 _load_fact 返回 None 自动跳过)
        return True

    # ------------------------------------------------------------------
    # prune_stale
    # ------------------------------------------------------------------

    def prune_stale(
        self,
        tenant_id: str = "default",
        max_age_days: int = 90,
        min_access_count: int = 1,
    ) -> int:
        """清"老 + 没人用"的 fact. pinned=True 不删."""
        cutoff_ts = time.time() - max_age_days * 86400.0
        ids = list(dict.fromkeys(self._backend.list_get(self._index_key(tenant_id))))
        deleted = 0
        for fid in ids:
            f = self._load_fact(tenant_id, str(fid))
            if f is None:
                continue
            if f.pinned:
                continue
            if f.last_accessed >= cutoff_ts:
                continue
            if f.access_count >= min_access_count:
                continue
            if self.delete_fact_in_tenant(fid, tenant_id):
                deleted += 1
        return deleted
