# -*- coding: utf-8 -*-
"""
long_term_memory_module — Agent 长期记忆 (Task DDD #90).

设计 (用户提议):
    1. 每条聊天信息进行信息提取 (LLM extract_facts, EEE #91 加)
    2. Agent 查历史时先检索 (search_facts)
    3. 查重: 存在就标记 (用户原话), 不重复存 (mark_accessed)
    4. 统一读取历史 (list_facts / search_facts 返回相关条目)

参考: MemGPT / Mem0 / Letta / ChatGPT memory 的现代 Agent 长期记忆模式.

API:
    LongTermMemoryImpl(backend=SqliteBackend("state/memory.db"))
        add_fact(fact)
        search_facts(query)
        list_facts(tenant_id, ...)
        delete_fact_in_tenant(fact_id, tenant_id)
        mark_accessed(fact_id)
        prune_stale(tenant_id, ...)
"""

from .core import BaseLongTermMemory, LongTermMemoryImpl
from .model import Fact, MemoryHit, MemoryQuery

__all__ = [
    "BaseLongTermMemory",
    "LongTermMemoryImpl",
    "Fact",
    "MemoryHit",
    "MemoryQuery",
]
