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
import math
import re
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

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
    """长期记忆默认实现, StateBackend 后端.

    Task EEE (#91): 加 optional embedder + llm_client 两个 DI 参数.
        embedder=None    → 不算 embedding, dedup 走 content_hash (DDD v0 路径)
        embedder=X       → add_fact 时算 embedding 存 fact, search/dedup 走 cosine
        llm_client=None  → extract_facts() 抛 NotImplementedError
        llm_client=X     → extract_facts() 用 LLM 抽 fact list
    similarity_threshold 默认 0.9 (用户指定).
    """

    def __init__(
        self,
        backend: "StateBackend",
        max_facts_per_tenant: int = 10000,
        embedder: Any = None,             # Task EEE: BaseEmbedding (embed_text -> List[float])
        llm_client: Any = None,           # Task EEE: BaseLLMService (generate -> str)
        similarity_threshold: float = 0.9,
        extract_max_facts: int = 5,
    ):
        if backend is None:
            raise ValueError("LongTermMemoryImpl 需要 backend (StateBackend), 不能为 None")
        self._backend = backend
        self._max_facts_per_tenant = int(max_facts_per_tenant)
        self._embedder = embedder
        self._llm_client = llm_client
        self._similarity_threshold = float(similarity_threshold)
        self._extract_max_facts = int(extract_max_facts)

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
        """新增 fact, 自动去重 (用户的 "存在就标记").

        去重二阶段:
            1. content_hash 精确匹配 (大小写不敏感, 极快)
            2. embedding cosine > threshold (EEE #91; embedder 为 None 时跳过)
        命中已有 → 不重复存, mark_accessed + tag 合并 + 返回已有 fact.
        """
        fact = fact.ensure_hash()
        tenant = fact.tenant_id or "default"

        # 一阶段 — content_hash 精确匹配
        existing_id = self._backend.get(self._hash_key(tenant, fact.content_hash))
        if existing_id:
            existing = self._load_fact(tenant, str(existing_id))
            if existing is not None:
                return self._bump_existing(existing, fact, reason="hash")

        # Task EEE (#91) — 二阶段语义查重 (cosine > threshold)
        if self._embedder is not None and fact.embedding is None:
            try:
                fact.embedding = self._embedder.embed_text(fact.content)
            except Exception:
                # embedder 失败不阻断 add, 直接落盘 (无 embedding 的 fact 仍可用 hash 路径)
                fact.embedding = None

        if fact.embedding:
            semantic_match = self._find_semantic_match(tenant, fact)
            if semantic_match is not None:
                return self._bump_existing(semantic_match, fact, reason="cosine")

        # 新 fact 落盘
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
        """检索最相关的 facts (3 阶段, 命中自动 mark_accessed):
            1. content_hash 精确命中 (cheap, 完全相同的内容)
            2. embedding cosine 排序 (EEE; embedder=None 时跳过)
            3. substring 包含兜底 (没 embedder 时主路径)
        """
        tenant = query.tenant_id or "default"
        hits: List[MemoryHit] = []
        seen_ids = set()

        # 一阶段 — content_hash 精确命中
        from long_term_memory_module.model.data_model import _content_hash
        exact_hash = _content_hash(query.query)
        exact_fact_id = self._backend.get(self._hash_key(tenant, exact_hash))
        if exact_fact_id:
            f = self._load_fact(tenant, str(exact_fact_id))
            if f and self._tag_filter_pass(f, query) and f.confidence >= query.min_confidence:
                hits.append(MemoryHit(fact=f, score=1.0, reason="content_hash_exact"))
                seen_ids.add(f.fact_id)
                self.mark_accessed(f.fact_id, tenant_id=tenant)

        # 二阶段 — embedding cosine (EEE #91)
        if self._embedder is not None and len(hits) < query.top_k:
            try:
                q_emb = self._embedder.embed_text(query.query)
            except Exception:
                q_emb = None
            if q_emb:
                scored: List[tuple[float, Fact]] = []
                ids = list(dict.fromkeys(self._backend.list_get(self._index_key(tenant))))
                for fid in ids:
                    if fid in seen_ids:
                        continue
                    f = self._load_fact(tenant, str(fid))
                    if f is None or not f.embedding:
                        continue
                    if not self._tag_filter_pass(f, query):
                        continue
                    if f.confidence < query.min_confidence:
                        continue
                    score = self._cosine_sim(q_emb, f.embedding)
                    if score > 0:
                        scored.append((score, f))
                # 取前 top_k - 已命中数
                scored.sort(key=lambda x: x[0], reverse=True)
                for score, f in scored:
                    if len(hits) >= query.top_k:
                        break
                    if f.fact_id in seen_ids:
                        continue
                    hits.append(MemoryHit(fact=f, score=score, reason=f"cosine_{score:.2f}"))
                    seen_ids.add(f.fact_id)
                    self.mark_accessed(f.fact_id, tenant_id=tenant)

        # 三阶段 — substring 兜底 (没 embedder 时主路径)
        if len(hits) < query.top_k:
            q_lower = query.query.lower().strip()
            ids = self._backend.list_get(self._index_key(tenant))
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
                    score = min(0.95, len(q_lower) / max(len(f.content), 1))
                    hits.append(MemoryHit(fact=f, score=score, reason="substring"))
                    seen_ids.add(f.fact_id)
                    self.mark_accessed(f.fact_id, tenant_id=tenant)
                    if len(hits) >= query.top_k:
                        break

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

    # ------------------------------------------------------------------
    # Task EEE (#91) — dedup helpers + LLM extraction
    # ------------------------------------------------------------------

    def _bump_existing(self, existing: Fact, new_fact: Fact, reason: str) -> Fact:
        """命中已有 → bump access_count + last_accessed + 合并 tags. 返回已有 fact."""
        existing.access_count += 1
        existing.last_accessed = time.time()
        # tags 合并
        tenant = existing.tenant_id
        for t in new_fact.tags:
            if t not in existing.tags:
                existing.tags.append(t)
                self._backend.list_append(
                    self._tag_index_key(tenant, t),
                    existing.fact_id,
                    maxlen=self._max_facts_per_tenant,
                )
        self._save_fact(existing)
        return existing

    @staticmethod
    def _cosine_sim(a: List[float], b: List[float]) -> float:
        """计算 cosine 相似度. 退化情况返回 0."""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for x, y in zip(a, b):
            dot += x * y
            na += x * x
            nb += y * y
        if na <= 0 or nb <= 0:
            return 0.0
        # 浮点误差可能让结果略 > 1 (e.g., 1.0000000000000002), clamp 到 [0,1]
        sim = dot / (math.sqrt(na) * math.sqrt(nb))
        return max(0.0, min(1.0, sim))

    def _find_semantic_match(self, tenant: str, new_fact: Fact) -> Optional[Fact]:
        """linear scan tenant fact 索引, 找 cosine > threshold 的最近邻.
        返回 None 表示没有近似 (走新增路径).

        性能: O(N facts × dim). N=1000, dim=384 → ~400K 乘加, ~10ms. 够用.
        N > 10000 时建议接 vector_db FAISS (留作未来优化).
        """
        if not new_fact.embedding:
            return None
        ids = list(dict.fromkeys(self._backend.list_get(self._index_key(tenant))))
        best_score = 0.0
        best_fact: Optional[Fact] = None
        for fid in ids:
            f = self._load_fact(tenant, str(fid))
            if f is None or not f.embedding:
                continue
            score = self._cosine_sim(new_fact.embedding, f.embedding)
            if score > best_score:
                best_score = score
                best_fact = f
        if best_score >= self._similarity_threshold:
            return best_fact
        return None

    def extract_facts(
        self,
        messages: List[Dict[str, str]],
        tenant_id: str = "default",
        session_id: Optional[str] = None,
    ) -> List[Fact]:
        """从对话消息抽 fact. messages 是 [{"role":"user|assistant", "content":"..."}, ...].

        Task EEE (#91): 用 LLM 抽取. 缺 llm_client 时抛 NotImplementedError
        (back-compat: DDD MVP 阶段不能用 extract_facts, 必须显式 add_fact).

        返回 List[Fact]; 调用方可逐一 add_fact 让本类做 dedup.
        """
        if self._llm_client is None:
            raise NotImplementedError(
                "LongTermMemoryImpl.extract_facts 需要 llm_client (BaseLLMService)"
            )
        if not messages:
            return []

        # 拼对话给 LLM
        formatted = "\n".join(
            f"[{m.get('role', 'user')}] {m.get('content', '')}"
            for m in messages
            if m.get("content")
        )
        if not formatted.strip():
            return []

        prompt = (
            "你是一个长期记忆抽取助手. 阅读下面的对话, 从中抽取值得 Agent 长期记住的"
            "事实 / 用户偏好 / 决策 / 上下文. 跳过寒暄和确认.\n\n"
            f"对话:\n{formatted}\n\n"
            f"返回 JSON 数组, 最多 {self._extract_max_facts} 条. 每条形如:\n"
            '  {"content": "...", "tags": ["preference"|"fact"|"decision"|"context"], '
            '"confidence": 0.0-1.0}\n'
            "规则:\n"
            "- content: 完整中文句子 (10-200 字)\n"
            "- tags: 1-3 个标签\n"
            "- confidence: 0-1 你的把握\n"
            "- 没值得记的就返回 []\n"
            "只返回 JSON 数组, 别加任何解释文字."
        )

        try:
            raw = self._llm_client.generate(prompt)
        except Exception as e:
            # LLM 失败不阻断主链路, 返回空 list
            return []

        items = self._parse_extracted_json(raw or "")
        out: List[Fact] = []
        for it in items[: self._extract_max_facts]:
            content = str(it.get("content") or "").strip()
            if len(content) < 4:  # 太短跳过
                continue
            tags = it.get("tags") or []
            if not isinstance(tags, list):
                tags = []
            try:
                conf = float(it.get("confidence", 1.0))
            except (TypeError, ValueError):
                conf = 1.0
            conf = max(0.0, min(1.0, conf))
            f = Fact.make(
                content=content,
                tenant_id=tenant_id,
                tags=[str(t) for t in tags],
                confidence=conf,
                session_id=session_id,
            )
            out.append(f)
        return out

    @staticmethod
    def _parse_extracted_json(raw: str) -> List[Dict[str, Any]]:
        """LLM 输出 JSON 数组, 但实际经常带 ```json 围栏或前后解释.
        策略: 先尝试直接 json.loads; 失败用正则抓第一个 `[ ... ]` 块再 loads."""
        raw = raw.strip()
        # 去掉可能的 ```json ... ``` markdown 围栏
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n", "", raw)
            raw = re.sub(r"\n```\s*$", "", raw)
            raw = raw.strip()
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, list) else []
        except Exception:
            pass
        # 正则抓首个 JSON 数组 (允许跨多行)
        m = re.search(r"\[[\s\S]*\]", raw)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, list) else []
        except Exception:
            return []
