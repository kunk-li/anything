# -*- coding: utf-8 -*-
"""
StateBackend ABC — cross-process 状态后端契约 (Task TT #80).

5 个核心操作覆盖 UsageTracker / ModelHealthTracker / QuotaGuard / ScheduledTaskRegistry
共同的存取模式:
    kv set/get          — 任意值 (模型 health / quota config)
    counter incr        — 数值原子增 (token 计数)
    list append + read  — bounded list (recent calls / rate limit window)
    clear               — 测试用

Backend 实现要保证:
    - 线程安全 (各方法可被多线程并发调用)
    - 进程安全 (SqliteBackend 通过 begin immediate + sqlite WAL 保证)
    - InMemoryBackend / SqliteBackend 是同步的 (Redis 异步实现留给 future Phase)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List, Optional


class StateBackend(ABC):
    """Cross-process state backend 契约."""

    # ---------- kv ----------
    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        """读 kv, 不存在返回 default."""
        ...

    @abstractmethod
    def set(self, key: str, value: Any) -> None:
        """写 kv, 覆盖原值."""
        ...

    # ---------- counter ----------
    @abstractmethod
    def incr(self, key: str, delta: float = 1.0) -> float:
        """原子增并返回新值. 不存在的 key 视为 0 起始."""
        ...

    # ---------- list ----------
    @abstractmethod
    def list_append(self, key: str, item: Any, maxlen: Optional[int] = None) -> None:
        """append 到 list 尾部. maxlen 不为 None 时, 超长从头部截.

        Args:
            key: list 标识
            item: JSON-serializable (str / dict / list / number)
            maxlen: 最大长度 (FIFO 清理); None 表示不限
        """
        ...

    @abstractmethod
    def list_get(self, key: str, limit: Optional[int] = None) -> List[Any]:
        """读 list 全部元素 (或最近 limit 条)."""
        ...

    # ---------- 通用 ----------
    @abstractmethod
    def clear(self, key_prefix: Optional[str] = None) -> None:
        """清空状态 (测试用). key_prefix 非空时只清匹配前缀的 key."""
        ...

    def close(self) -> None:
        """关闭底层连接 (sqlite 句柄 / redis pool). 默认 no-op."""
        pass
