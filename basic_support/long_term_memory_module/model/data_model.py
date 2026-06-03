# -*- coding: utf-8 -*-
"""
Long-term Memory 数据模型 (Task DDD #90).

3 个 pydantic schema:
    Fact         抽取出的原子事实, 进 fact 库的最小单位
    MemoryQuery  Agent 查询历史记忆时的 input
    MemoryHit    search_facts() 返回的命中, fact + score + 命中原因
"""
from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _now() -> float:
    return time.time()


def _fact_id() -> str:
    """随机 fact_id, 12 字符够避碰."""
    return uuid.uuid4().hex[:12]


def _content_hash(content: str) -> str:
    """content 的 sha1 短 hash. 用于 DDD MVP 阶段的去重 (EEE 改 embedding 语义判重)."""
    return hashlib.sha1((content or "").strip().lower().encode("utf-8")).hexdigest()[:16]


class Fact(BaseModel):
    """单条长期记忆事实.

    Lifecycle:
      add_fact(content, ...) → Fact(fact_id 自动生成, last_accessed=now)
      search_facts(query) 命中 → mark_accessed → access_count++ / last_accessed=now
      prune_stale 周期清理 access_count=0 & age > N days

    分类 tag 推荐 (字符串自由约定):
      "preference"   用户偏好, 例: 用户喜欢 Python over JS
      "fact"         客观事实, 例: 项目部署在 AWS us-east-1
      "decision"     决策, 例: 选用 SQLite 而不是 Redis
      "context"      上下文, 例: 用户在 frontend team
    """

    fact_id: str = Field(default_factory=_fact_id, min_length=1)
    content: str = Field(min_length=1)
    content_hash: str = ""  # 自动从 content 派生, 用于 DDD MVP hash 去重

    # 关联溯源
    source_message_id: Optional[str] = None  # 这条 fact 出自哪条原始消息
    session_id: Optional[str] = None
    tenant_id: str = "default"

    # 时间 / 使用统计
    created_at: float = Field(default_factory=_now)
    last_accessed: float = Field(default_factory=_now)
    access_count: int = 0

    # 元数据
    tags: List[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)  # LLM 抽取时给的置信度
    pinned: bool = False  # 用户手动置顶, prune 时不删
    embedding: Optional[List[float]] = None  # EEE 加入; DDD 阶段 None

    # 方向2 (记忆升级): 可变性分层 + 精炼/原始双层
    mutability: str = "refinable"   # canonical(固定权威:密码/路径/配置, 不改写不过期) | refinable(偏好/做法, 可提炼可丢原始)
    digest: str = ""                # 精炼层: 一句话摘要, 两级检索的粗筛用; 空则回退 content
    content_type: str = ""          # 细分类: secret/path/config/preference/decision/fact/context...
    encrypted: bool = False         # content 是否加密存储 (敏感 canonical secret, 见 MEM-3)

    model_config = {"extra": "ignore"}

    def ensure_hash(self) -> "Fact":
        """如果 content_hash 没设, 从 content 自动派生."""
        if not self.content_hash:
            self.content_hash = _content_hash(self.content)
        return self

    @classmethod
    def make(
        cls,
        content: str,
        tenant_id: str = "default",
        **kwargs: Any,
    ) -> "Fact":
        """便捷构造, 自动算 content_hash."""
        f = cls(content=content.strip(), tenant_id=tenant_id, **kwargs)
        return f.ensure_hash()


class MemoryQuery(BaseModel):
    """Agent 查询长期记忆的 input."""

    query: str = Field(min_length=1)
    tenant_id: str = "default"
    top_k: int = Field(default=5, ge=1, le=50)
    tags_filter: Optional[List[str]] = None  # 限定 tag, 例: 只查 preference
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    model_config = {"extra": "ignore"}


class MemoryHit(BaseModel):
    """search_facts 返回的命中条目."""

    fact: Fact
    score: float = Field(ge=0.0, le=1.0)  # 相关度 0~1
    reason: str = ""  # "content_hash_exact" / "cosine_0.92" / "tag_match"

    model_config = {"extra": "ignore"}
