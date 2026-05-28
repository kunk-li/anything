# -*- coding: utf-8 -*-
"""
state_backend_module — 跨进程状态后端抽象 (Task TT #80).

把进程内 4 个单例 (UsageTracker / ModelHealthTracker / QuotaGuard /
ScheduledTaskRegistry) 共有的 "存 counter / list / kv" 操作收敛到一个
StateBackend ABC, 默认 InMemoryBackend 跟现在一样进程内; 同时提供
SQLiteBackend (跨进程持久化) 和 RedisBackend stub (高吞吐生产场景).

为什么需要这个抽象:
  - gunicorn / uvicorn 多 worker 时, 每个 worker 进程独立维护 _default
    单例, 导致 token 计数 / 限流窗口分裂
  - 容器重启时, 进程内状态全丢
  - admin 面板汇总不准 (只看到当前 worker 的视图)

设计取舍 — 加性, 不强迫迁移:
  - 各 tracker 暂时不动 (Phase 1 只建抽象 + 实现)
  - tracker 后续 (Phase 2) 加 optional backend 参数, default=InMemoryBackend()
  - 业务零感知, 单测 0 改动

用法 (Phase 2 待实现):
  >>> from state_backend_module import SqliteBackend
  >>> tracker = UsageTracker(backend=SqliteBackend(path="state/usage.db"))
"""

from .base import StateBackend
from .impl import InMemoryBackend, SqliteBackend, RedisBackend

__all__ = [
    "StateBackend",
    "InMemoryBackend",
    "SqliteBackend",
    "RedisBackend",
]
