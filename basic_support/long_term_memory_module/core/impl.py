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
        secret_key: Optional[str] = None,  # MEM-3: 敏感 secret 加密密钥
    ):
        if backend is None:
            raise ValueError("LongTermMemoryImpl 需要 backend (StateBackend), 不能为 None")
        self._backend = backend
        self._max_facts_per_tenant = int(max_facts_per_tenant)
        self._embedder = embedder
        self._llm_client = llm_client
        self._similarity_threshold = float(similarity_threshold)
        self._extract_max_facts = int(extract_max_facts)
        # MEM-3: 敏感 secret 加密密钥 (DI 优先, 否则取环境 SENSITIVE_CONFIG_SECRET)
        import os
        self._secret_key = secret_key or os.environ.get("SENSITIVE_CONFIG_SECRET") or None

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
                # MEM-3: secret 用 digest 算 embedding (明文可检索), 不用即将加密的 content
                embed_target = (fact.digest if (fact.content_type == "secret" and fact.digest)
                                else fact.content)
                fact.embedding = self._embedder.embed_text(embed_target)
            except Exception:
                # embedder 失败不阻断 add, 直接落盘 (无 embedding 的 fact 仍可用 hash 路径)
                fact.embedding = None

        # secret 的语义去重会因 digest 相似而误判不同的值 → secret 只靠 content_hash 精确去重
        if fact.embedding and fact.content_type != "secret":
            semantic_match = self._find_semantic_match(tenant, fact)
            if semantic_match is not None:
                return self._bump_existing(semantic_match, fact, reason="cosine")

        # MEM-3: 敏感 secret 落盘前保护 content (加密 or 无密钥时丢弃明文)
        fact = self._maybe_protect_secret(fact)
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
    # MEM-3: 敏感 secret 加密 / 解密 (复用 config_module 的 Fernet)
    # ------------------------------------------------------------------

    def _maybe_protect_secret(self, fact: Fact) -> Fact:
        """敏感 canonical(content_type=secret) 落盘前保护 content。
        有密钥 → 加密 + encrypted=True; 无密钥/加密失败 → 丢弃明文只留 digest
        (绝不明文落库)。content_hash 已基于明文算过(去重不受影响)。"""
        if fact.content_type != "secret" or fact.encrypted:
            return fact
        if self._secret_key:
            try:
                from config_module.utils.tool_functions import encrypt_value
                fact.content = encrypt_value(fact.content, self._secret_key)
                fact.encrypted = True
                return fact
            except Exception:
                pass  # 加密失败 → 落到下面"绝不明文落库"分支
        fact.content = "[敏感值未存储: 未配置加密密钥或加密失败]"
        fact.encrypted = False
        return fact

    def reveal_fact(self, fact_id: str, tenant_id: str = "default") -> Optional[str]:
        """显式解密一条加密 fact 的真实 content (需要真实值时才调)。
        非加密 → 直接返回 content; 无密钥 / 解密失败 → None。"""
        fact = self._load_fact(tenant_id, fact_id)
        if fact is None:
            return None
        if not fact.encrypted:
            return fact.content
        if not self._secret_key:
            return None
        try:
            from config_module.utils.tool_functions import decrypt_value
            return decrypt_value(fact.content, self._secret_key)
        except Exception:
            return None

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
            # 方向1 阶段3: superseded(被新偏好取代)的不作为当前上下文返回
            if (f and f.superseded_by is None and self._tag_filter_pass(f, query)
                    and f.confidence >= query.min_confidence):
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
                    if f.superseded_by is not None:   # 方向1 阶段3: 跳过被取代的旧偏好
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

        # 三阶段 — 两级检索 (MEM-5): 先在 digest(精炼层)粗筛"主题相关", 再用 content(原始层)细比确认。
        # digest+content 双命中最可信; 仅 content 命中次之; 仅 digest 命中(主题沾边、细节未必/已加密) 给中分。
        # 副作用: 加密 secret 的 content 是密文(搜不到), 但 digest 明文("X的密码")可被检索 → digest_only 命中。
        if len(hits) < query.top_k:
            q_lower = query.query.lower().strip()
            ids = self._backend.list_get(self._index_key(tenant))
            for fid in dict.fromkeys(ids):
                if fid in seen_ids:
                    continue
                f = self._load_fact(tenant, str(fid))
                if (f is None or f.superseded_by is not None    # 方向1 阶段3: 跳过被取代的旧偏好
                        or not self._tag_filter_pass(f, query) or f.confidence < query.min_confidence):
                    continue
                hit_digest = bool(f.digest) and q_lower in f.digest.lower()    # L1 粗筛: 精炼层
                hit_content = q_lower in f.content.lower()                     # L2 细比: 原始层
                if not (hit_digest or hit_content):
                    continue
                if hit_digest and hit_content:
                    score, reason = min(0.95, len(q_lower) / max(len(f.content), 1) + 0.3), "digest+content"
                elif hit_content:
                    score, reason = min(0.9, len(q_lower) / max(len(f.content), 1)), "substring"
                else:
                    score, reason = 0.5, "digest_only"   # 仅精炼层命中: 主题相关, 细节需细看/reveal
                hits.append(MemoryHit(fact=f, score=score, reason=reason))
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
            if f is None or f.pinned:
                continue
            if f.mutability == "canonical":   # MEM-6: 固定权威(密码/路径)永久保留, 永不删
                continue
            if f.last_accessed >= cutoff_ts:
                continue
            if f.access_count >= min_access_count:
                continue
            if self.delete_fact_in_tenant(fid, tenant_id):
                deleted += 1
        return deleted

    def degrade_stale_refinable(self, tenant_id: str = "default", max_age_days: int = 90) -> int:
        """MEM-6: refinable 老 fact 丢弃 content 原始、保留 digest 精炼 (兑现用户需求⑤
        "多余很久的可提炼部分原始信息可丢弃")。canonical / pinned / 无 digest 的不动。
        返回 degrade 了几条。"""
        DEGRADED = "[原始已清理, 仅保留精炼摘要]"
        cutoff_ts = time.time() - max_age_days * 86400.0
        ids = list(dict.fromkeys(self._backend.list_get(self._index_key(tenant_id))))
        degraded = 0
        for fid in ids:
            f = self._load_fact(tenant_id, str(fid))
            if f is None or f.pinned or f.mutability == "canonical":
                continue
            if not f.digest or f.content == DEGRADED:
                continue  # 没精炼可留(留给 prune 删) 或已 degrade 过
            if f.last_accessed >= cutoff_ts:
                continue
            f.content = DEGRADED
            self._save_fact(f)
            degraded += 1
        return degraded

    def consolidate(self, tenant_id: str = "default", max_facts: int = 20) -> int:
        """MEM-7: 取 refinable facts, 用 LLM 把相关/重复的多条归纳成更高层精炼
        (如多次"选 SQLite" → 偏好"倾向 SQLite")。无 llm_client → 跳过返回 0。
        归纳结果走 add_fact (自带 dedup)。返回新增归纳条数。"""
        if self._llm_client is None:
            return 0
        refinable = [f for f in self.list_facts(tenant_id, limit=max_facts)
                     if f.mutability == "refinable"]
        if len(refinable) < 2:
            return 0
        listing = "\n".join(f"- {f.content}" for f in refinable)
        prompt = (
            "下面是一批长期记忆事实。把语义相关/重复的归纳成更高层、更精炼的偏好或结论。\n\n"
            f"{listing}\n\n"
            '返回 JSON 数组, 每条 {"content":"归纳后的更高层结论", "digest":"一句话精炼", "tags":["preference"]}。\n'
            "只归纳确实相关的多条; 没有可归纳的返回 []。只返回 JSON 数组。"
        )
        try:
            raw = self._llm_client.generate(prompt)
        except Exception:
            return 0
        added = 0
        for it in self._parse_extracted_json(raw or ""):
            content = str(it.get("content") or "").strip()
            if len(content) < 4:
                continue
            self.add_fact(Fact.make(
                content=content, tenant_id=tenant_id, mutability="refinable",
                digest=str(it.get("digest") or "").strip(),
                tags=[str(t) for t in (it.get("tags") or ["preference"])],
                content_type="preference",
            ))
            added += 1
        return added

    # ------------------------------------------------------------------
    # UP-1 (方向1): 用户画像聚合 — 供 agent 任务前 always-on 注入
    # ------------------------------------------------------------------

    def get_user_profile(self, tenant_id: str = "default", per_dim: int = 3) -> Dict[str, List[str]]:
        """聚合用户画像。按 5 维度(preference/style/convention/domain/weakness)取该 tenant
        的有效 fact, 每维度 top per_dim 条精炼(digest 优先)。
        返回 {维度: [精炼条目...]}, 空维度不含。用于"这个人怎么做事"的 always-on 注入。

        weakness = 使用者需 agent 主动补位的点(客观中性), 注入后 agent 据此规避缺陷。

        方向1 阶段3 (时效): 排序按 (pinned, created_at, access_count) — 新学到的偏好领先,
        偏好变了时新 fact 挤掉旧的(per_dim 截断); 已被 reconcile_conflicts 标 superseded 的不取。
        用 created_at(学到的时刻)而非 last_accessed(会被检索 bump 污染)作时效信号。
        """
        DIMS = ("preference", "style", "convention", "domain", "weakness")
        facts = [f for f in self.list_facts(tenant_id, limit=200) if f.superseded_by is None]
        profile: Dict[str, List[str]] = {}
        for dim in DIMS:
            matched = [f for f in facts if f.content_type == dim or dim in f.tags]
            matched.sort(key=lambda f: (1 if f.pinned else 0, f.created_at, f.access_count), reverse=True)
            if matched:
                profile[dim] = [(f.digest or f.content)[:100] for f in matched[:per_dim]]
        return profile

    def reconcile_conflicts(self, tenant_id: str = "default", max_facts: int = 40) -> int:
        """方向1 阶段3: 消解画像里自相矛盾的偏好 (用户偏好变了 → 旧的让位新的)。

        取有效(未 superseded) refinable facts, 让 LLM 找出"互相对立"的分组
        (如 '偏好详细解释' vs '偏好简洁回答')。每组里 created_at 最新的视为当前偏好(胜),
        其余标 superseded_by=胜者 fact_id (不删, 保审计链; 此后不再进画像/检索)。

        无 llm_client → 跳过返回 0 (时效排序仍让新偏好在 get_user_profile 领先, 见该方法)。
        与 consolidate 的区别: consolidate 归纳"相关/重复"的多条成更高层; reconcile 消解"对立"的多条。
        循 consolidate 的"独立 LLM 批处理"模式 (admin/scheduler 触发), 不塞进每轮 add_fact。
        返回标记为 superseded 的条数。
        """
        if self._llm_client is None:
            return 0
        active = [f for f in self.list_facts(tenant_id, limit=max_facts)
                  if f.mutability == "refinable" and f.superseded_by is None]
        if len(active) < 2:
            return 0
        listing = "\n".join(f"[{i}] {f.digest or f.content}" for i, f in enumerate(active))
        prompt = (
            "下面是一个用户的若干长期偏好/做法记忆, 每条带编号。\n"
            "找出其中【互相对立/矛盾】的分组(同一件事上前后说法相反, 说明用户改了主意), "
            "例如 '偏好详细解释' 与 '偏好简洁回答' 对立。\n"
            "只把【真的对立】的归到一组; 只是不同方面、并不矛盾的不要分组。\n\n"
            f"{listing}\n\n"
            "返回 JSON 数组, 每个元素是一组对立条目的编号数组, 如 [[0,3],[1,4]]。\n"
            "没有任何对立 → 返回 []。只返回 JSON。"
        )
        try:
            raw = self._llm_client.generate(prompt)
        except Exception:
            return 0
        superseded = 0
        for group in self._parse_extracted_json(raw or ""):
            if not isinstance(group, list):
                continue   # 防 LLM 把数组拍平 → 单个编号不构成"组", 跳过 (fail-safe)
            idxs = list(dict.fromkeys(
                int(i) for i in group
                if isinstance(i, (int, float)) and 0 <= int(i) < len(active)
            ))
            if len(idxs) < 2:
                continue
            winner = max(idxs, key=lambda i: active[i].created_at)   # 组内最新者 = 当前偏好
            for i in idxs:
                if i == winner or active[i].superseded_by is not None:
                    continue
                active[i].superseded_by = active[winner].fact_id
                self._save_fact(active[i])
                superseded += 1
        return superseded

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
            "你是一个长期记忆抽取助手. 阅读下面的对话, 抽取值得 Agent 长期记住的"
            "事实 / 偏好 / 决策 / 关键信息(密码·路径·配置等). 跳过寒暄和确认.\n\n"
            f"对话:\n{formatted}\n\n"
            f"返回 JSON 数组, 最多 {self._extract_max_facts} 条. 每条形如:\n"
            '  {"content": "原始完整信息", "digest": "一句话精炼", '
            '"mutability": "canonical"|"refinable", '
            '"content_type": "secret|path|config|preference|style|convention|domain|weakness|fact|decision|context", '
            '"tags": [...], "confidence": 0.0-1.0}\n'
            "规则:\n"
            "- content: 完整原始信息; 密码/路径/配置要一字不差精确保留\n"
            "- digest: 一句话精炼, 用于快速检索'这是关于什么的'; 密码类写'X的密码'不要写出密码值本身\n"
            "- mutability: canonical=固定权威不该被改写(密码/路径/配置/ID/确定的决策); "
            "refinable=可随时间归纳提炼(偏好/习惯/做法)\n"
            "- content_type: 内容细分类; 用户画像 5 维度=preference(偏好)/style(做事·沟通风格)/"
            "convention(约定·规矩)/domain(领域·在做什么)/weakness(使用者需 agent 主动补位的点, "
            "客观中性、不贬低)\n"
            "- confidence: 0-1 你的把握\n"
            "- 没值得记的就返回 []\n"
            "只返回 JSON 数组, 别加任何解释文字."
        )

        try:
            raw = self._llm_client.generate(prompt)
        except Exception:
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
            mutability = str(it.get("mutability") or "refinable").lower()
            if mutability not in ("canonical", "refinable"):
                mutability = "refinable"
            f = Fact.make(
                content=content,
                tenant_id=tenant_id,
                tags=[str(t) for t in tags],
                confidence=conf,
                session_id=session_id,
                mutability=mutability,
                digest=str(it.get("digest") or "").strip(),
                content_type=str(it.get("content_type") or "").strip(),
            )
            out.append(f)
        return out

    # ------------------------------------------------------------------
    # Task JJJ (#96): 对话上下文压缩 — MemGPT working / archival 分层
    # ------------------------------------------------------------------

    def compress_history(
        self,
        messages: List[Dict[str, str]],
        keep_recent: int = 5,
        max_total_chars: int = 8000,
        tenant_id: str = "default",
        session_id: Optional[str] = None,
        archive_facts: bool = True,
    ) -> tuple:
        """把超长对话历史压缩成 [summary + 最近 K 轮].

        触发条件 (任一满足):
            len(messages) > keep_recent + 2  (至少有 3 轮可被总结才有意义)
            OR  sum(len(m.content)) > max_total_chars

        步骤:
            1. 切分 messages 为 (old = 老的, recent = 最近 keep_recent 条)
            2. archive_facts=True 时 extract_facts(old) 把可保留的事实持久化
            3. LLM 总结 old → "[历史对话摘要] ..."
            4. 返回 [{role:system, content:summary}, ...recent], summary_text

        失败 (LLM 不可用 / 抽取失败) → 退化截断, 返 (messages[-keep_recent:], None).

        返回:
            (compressed_messages, summary_or_None)
        """
        if not messages:
            return messages, None

        total_chars = sum(len(m.get("content", "") or "") for m in messages)
        need_compress = (
            len(messages) > keep_recent + 2
            or total_chars > max_total_chars
        )
        if not need_compress:
            return messages, None

        # 1. split
        if len(messages) <= keep_recent:
            return messages, None
        old = messages[:-keep_recent]
        recent = messages[-keep_recent:]
        if not old:
            return messages, None

        # 2. archive_facts — 抽老对话的事实, 不阻断主路径
        if archive_facts and self._llm_client is not None:
            try:
                facts = self.extract_facts(
                    messages=old, tenant_id=tenant_id, session_id=session_id,
                )
                for f in facts:
                    try:
                        self.add_fact(f)
                    except Exception:
                        pass
            except Exception:
                pass

        # 3. LLM 总结 old
        summary = self._summarize_for_compression(old)
        if not summary:
            # LLM 不可用 → 退化截断
            return recent, None

        system_msg = {
            "role": "system",
            "content": f"[历史对话摘要 (老 {len(old)} 轮已压缩)]\n{summary}",
        }
        return [system_msg, *recent], summary

    def _summarize_for_compression(self, messages: List[Dict[str, str]]) -> str:
        """LLM 总结一段对话. 失败返空串."""
        if self._llm_client is None or not messages:
            return ""
        formatted = "\n".join(
            f"[{m.get('role', 'user')}] {(m.get('content') or '')[:600]}"
            for m in messages
            if m.get("content")
        )
        if not formatted.strip():
            return ""
        prompt = (
            "请把下面的对话历史浓缩成 3-8 句的中文摘要, 保留:\n"
            "- 用户的主要诉求 / 提到的关键名词\n"
            "- 已经确认的事实 / 决策 / 偏好\n"
            "- 助手已给出的关键答复\n"
            "不要复述寒暄, 不要加 markdown 列表项, 写连贯段落.\n\n"
            f"[对话历史]\n{formatted}\n\n"
            "摘要:"
        )
        try:
            out = self._llm_client.generate(prompt)
            return (out or "").strip()[:2000]
        except Exception:
            return ""

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
