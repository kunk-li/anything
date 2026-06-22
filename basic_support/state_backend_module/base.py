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
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional


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

    # ---------- 原子复合更新 ----------
    @contextmanager
    def transaction(self) -> Iterator["StateBackend"]:
        """让一组 get/set/incr/list_* 调用整体原子地执行 (读改写不被打断).

        调用方需要 "读状态 → 据此改多个 key" 这种复合更新时, 单个 op 各自原子并不够:
        op 与 op 之间状态会被别的线程/进程插入修改, 造成 lost-update / 状态机串味
        (例: ModelHealthTracker 在 backend 模式下并发 record_success / record_failure
        会把 state 与 consecutive_failures 写成互相矛盾的值).

        with backend.transaction():
            cur = backend.get(k)
            backend.set(k, f(cur))      # 整段内不会被别的 writer 插入

        语义保证:
            - 进入到退出之间, 同一 backend 上的其它写操作被阻塞 (互斥)
            - 块内的 get/set/incr/list_* 是同一事务的一部分, 全部提交或全部回滚
            - 可重入: transaction() 块内再调 transaction() 不会死锁/嵌套事务报错

        默认实现是 no-op (单 op 后端 / RedisBackend stub 不提供组级原子性).
        需要跨线程 / 跨进程原子性的后端 (InMemoryBackend / SqliteBackend) 覆写本方法.
        """
        yield self

    def close(self) -> None:
        """关闭底层连接 (sqlite 句柄 / redis pool). 默认 no-op."""
        pass
