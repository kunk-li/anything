# -*- coding: utf-8 -*-
"""
BaseLongTermMemory ABC (Task DDD #90).

约定长期记忆 module 的 6 个核心动作; 不同 impl 可以替 storage / extraction /
similarity 计算的策略, ABC 只钉签名.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from long_term_memory_module.model import Fact, MemoryHit, MemoryQuery


class BaseLongTermMemory(ABC):
    """Long-term memory 抽象层."""

    @abstractmethod
    def add_fact(self, fact: Fact) -> Fact:
        """新增一条 fact. 如已存在 (按 content_hash / embedding cosine) 应做
        dedup 处理 (bump access_count + last_accessed, 不重复存).
        返回最终 fact (可能是新插入的, 也可能是已有的被 bump 的).
        """
        ...

    @abstractmethod
    def search_facts(self, query: MemoryQuery) -> List[MemoryHit]:
        """按 query 查最相关的 top_k facts, 每个命中带 score + reason.
        命中时应自动调 mark_accessed (用户的设计: "存在就标记")."""
        ...

    @abstractmethod
    def mark_accessed(self, fact_id: str) -> bool:
        """显式标记一条 fact 被访问. access_count + 1, last_accessed = now.
        返回 True 如果 fact 存在."""
        ...

    @abstractmethod
    def list_facts(
        self,
        tenant_id: str = "default",
        limit: int = 100,
        offset: int = 0,
        tags_filter: Optional[List[str]] = None,
    ) -> List[Fact]:
        """枚举 tenant 下所有 facts, 按 last_accessed 倒序."""
        ...

    @abstractmethod
    def delete_fact(self, fact_id: str) -> bool:
        """删除一条 fact. 返回 True 如果原本存在."""
        ...

    @abstractmethod
    def prune_stale(
        self,
        tenant_id: str = "default",
        max_age_days: int = 90,
        min_access_count: int = 1,
    ) -> int:
        """清理"老 + 没人用"的 facts. pinned=True 不删. 返回删了几条."""
        ...
