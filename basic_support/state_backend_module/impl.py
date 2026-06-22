# -*- coding: utf-8 -*-
"""
3 种 StateBackend 实现 (Task TT #80):
    InMemoryBackend  — 默认, 进程内 dict + threading.Lock, 跟今天行为一致
    SqliteBackend    — 跨进程持久化, stdlib sqlite3 + WAL + busy_timeout
    RedisBackend     — 高吞吐生产场景, stub (需 redis-py, 装上即用)
"""
from __future__ import annotations

import json
import sqlite3
import threading
from collections import deque
from contextlib import contextmanager
from typing import Any, Deque, Dict, Iterator, List, Optional

from .base import StateBackend


# ---------------------------------------------------------------------------
# InMemoryBackend — default
# ---------------------------------------------------------------------------


class InMemoryBackend(StateBackend):
    """进程内 dict + Lock, 跟今天各 tracker 内部状态等价.

    不跨进程, 不持久化. 用于:
        - 单进程开发 / 单元测试
        - 不需要跨进程汇总的场景
    """

    def __init__(self) -> None:
        self._kv: Dict[str, Any] = {}
        self._lists: Dict[str, Deque[Any]] = {}
        # RLock (非 Lock): transaction() 持锁期间块内还会再调 get/set/incr, 这些方法
        # 各自也取同一把锁, 需要可重入否则自锁死.
        self._lock = threading.RLock()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._kv.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._kv[key] = value

    def incr(self, key: str, delta: float = 1.0) -> float:
        with self._lock:
            cur = float(self._kv.get(key, 0))
            new = cur + float(delta)
            self._kv[key] = new
            return new

    def list_append(self, key: str, item: Any, maxlen: Optional[int] = None) -> None:
        with self._lock:
            dq = self._lists.get(key)
            if dq is None or (maxlen is not None and dq.maxlen != maxlen):
                # 切换 maxlen 时重建 deque (带新 maxlen)
                old_items = list(dq) if dq else []
                dq = deque(old_items, maxlen=maxlen)
                self._lists[key] = dq
            dq.append(item)

    def list_get(self, key: str, limit: Optional[int] = None) -> List[Any]:
        with self._lock:
            dq = self._lists.get(key)
            if not dq:
                return []
            items = list(dq)
            if limit is not None and limit < len(items):
                return items[-limit:]
            return items

    def clear(self, key_prefix: Optional[str] = None) -> None:
        with self._lock:
            if key_prefix is None:
                self._kv.clear()
                self._lists.clear()
                return
            for k in [k for k in self._kv if k.startswith(key_prefix)]:
                self._kv.pop(k, None)
            for k in [k for k in self._lists if k.startswith(key_prefix)]:
                self._lists.pop(k, None)

    @contextmanager
    def transaction(self) -> Iterator[StateBackend]:
        # 持锁整段 — 块内 get/set/incr/list_* 取同一把 RLock, 期间无别的线程能写.
        with self._lock:
            yield self


# ---------------------------------------------------------------------------
# SqliteBackend — cross-process via WAL mode
# ---------------------------------------------------------------------------


class SqliteBackend(StateBackend):
    """SQLite + WAL 让多 worker 进程共享状态 + 重启不丢.

    Schema (lazy create):
        kv (k TEXT PRIMARY KEY, v TEXT)              — JSON-encoded value
        lst (k TEXT, seq INTEGER, v TEXT)            — 自增 seq, list_get ORDER BY seq

    并发: SQLite WAL 模式让 reader 不阻塞 writer; busy_timeout 让 writer 之间排队
          而不是立刻 SQLITE_BUSY. incr/list_append 在 BEGIN IMMEDIATE 事务内做读改写,
          所以是原子的.
    """

    def __init__(self, path: str = "state_backend.db", busy_timeout_ms: int = 5000):
        self._path = path
        self._busy_timeout_ms = busy_timeout_ms
        # check_same_thread=False — 我们自己用 Lock 串行化访问 (跨线程共享 Connection 在 Py3.6+ 安全)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS lst ("
            "  k TEXT NOT NULL,"
            "  seq INTEGER NOT NULL,"
            "  v TEXT NOT NULL,"
            "  PRIMARY KEY (k, seq)"
            ")"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS lst_k_seq ON lst (k, seq)")
        # RLock: transaction() 持锁期间块内再调 incr/list_append/get/set 取同一把锁.
        self._lock = threading.RLock()
        # transaction() 进入时置 True — 让 incr/list_append 跳过自己的 BEGIN/COMMIT,
        # 复用外层那一个事务 (sqlite 不允许嵌套 BEGIN).
        self._in_txn = False

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except Exception:
            return row[0]  # 兜底返回原文

    def set(self, key: str, value: Any) -> None:
        encoded = json.dumps(value, default=str)
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (key, encoded),
            )

    def incr(self, key: str, delta: float = 1.0) -> float:
        delta = float(delta)
        with self._lock:
            # 已在外层 transaction() 内 → 复用那个事务, 不再自己 BEGIN/COMMIT.
            if self._in_txn:
                return self._incr_locked(key, delta)
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                new = self._incr_locked(key, delta)
                self._conn.execute("COMMIT")
                return new
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def _incr_locked(self, key: str, delta: float) -> float:
        """read-modify-write 主体 (不管理事务边界). 必须在持锁 + 事务内调用."""
        row = self._conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        cur = 0.0
        if row is not None:
            try:
                cur = float(json.loads(row[0]))
            except Exception:
                cur = 0.0
        new = cur + delta
        self._conn.execute(
            "INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, json.dumps(new)),
        )
        return new

    def list_append(self, key: str, item: Any, maxlen: Optional[int] = None) -> None:
        encoded = json.dumps(item, default=str)
        with self._lock:
            # 已在外层 transaction() 内 → 复用那个事务, 不再自己 BEGIN/COMMIT.
            if self._in_txn:
                self._list_append_locked(key, encoded, maxlen)
                return
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._list_append_locked(key, encoded, maxlen)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def _list_append_locked(self, key: str, encoded: str, maxlen: Optional[int]) -> None:
        """append 主体 (不管理事务边界). 必须在持锁 + 事务内调用."""
        # 取当前最大 seq + 1
        row = self._conn.execute("SELECT COALESCE(MAX(seq), -1) FROM lst WHERE k=?", (key,)).fetchone()
        next_seq = (row[0] if row else -1) + 1
        self._conn.execute(
            "INSERT INTO lst(k, seq, v) VALUES(?,?,?)",
            (key, next_seq, encoded),
        )
        # 超长时 FIFO 截
        if maxlen is not None and maxlen > 0:
            count = self._conn.execute("SELECT COUNT(*) FROM lst WHERE k=?", (key,)).fetchone()[0]
            if count > maxlen:
                excess = count - maxlen
                # 删除 seq 最小的 excess 条
                self._conn.execute(
                    "DELETE FROM lst WHERE rowid IN ("
                    "  SELECT rowid FROM lst WHERE k=? ORDER BY seq ASC LIMIT ?"
                    ")",
                    (key, excess),
                )

    def list_get(self, key: str, limit: Optional[int] = None) -> List[Any]:
        with self._lock:
            if limit is None:
                rows = self._conn.execute(
                    "SELECT v FROM lst WHERE k=? ORDER BY seq ASC", (key,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT v FROM lst WHERE k=? ORDER BY seq DESC LIMIT ?",
                    (key, int(limit)),
                ).fetchall()
                rows = list(reversed(rows))
        out: List[Any] = []
        for r in rows:
            try:
                out.append(json.loads(r[0]))
            except Exception:
                out.append(r[0])
        return out

    def clear(self, key_prefix: Optional[str] = None) -> None:
        with self._lock:
            if key_prefix is None:
                self._conn.execute("DELETE FROM kv")
                self._conn.execute("DELETE FROM lst")
                return
            pattern = key_prefix + "%"
            self._conn.execute("DELETE FROM kv WHERE k LIKE ?", (pattern,))
            self._conn.execute("DELETE FROM lst WHERE k LIKE ?", (pattern,))

    @contextmanager
    def transaction(self) -> Iterator[StateBackend]:
        with self._lock:
            # 可重入: 已在事务内则直接 yield, 不再开新事务 (sqlite 不允许嵌套 BEGIN).
            if self._in_txn:
                yield self
                return
            # BEGIN IMMEDIATE: 立刻拿写锁, 跨进程 (WAL + busy_timeout) 让别的 worker 排队,
            # 整段读改写不被插入 → 跟 in-memory 单锁不变式一致.
            self._conn.execute("BEGIN IMMEDIATE")
            self._in_txn = True
            try:
                yield self
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            finally:
                self._in_txn = False

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# RedisBackend — high-throughput stub
# ---------------------------------------------------------------------------


class RedisBackend(StateBackend):
    """Redis 实现 stub. 需要 pip install redis. 不强依赖, 调用前自查 import.

    暂留 NotImplementedError, 但接口完整 (调用方写代码时 IDE 能补全).
    打开方式 (Phase 2 / 用户按需):
        把每个 method 改为对应 redis 命令:
            get   → redis.get
            set   → redis.set
            incr  → redis.incrbyfloat
            list_append → redis.rpush + (maxlen 时) redis.ltrim
            list_get → redis.lrange
            clear → redis.flushdb (注意全清 vs 前缀 keys+del)
    """

    def __init__(self, url: str = "redis://localhost:6379/0", **kwargs):
        self.url = url
        self._kwargs = kwargs
        try:
            import redis  # type: ignore
            self._redis = redis.from_url(url, **kwargs)
        except ImportError as e:
            raise RuntimeError(
                "RedisBackend 需要 redis-py: pip install redis"
            ) from e

    def get(self, key: str, default: Any = None) -> Any:
        raise NotImplementedError("RedisBackend Phase 2: 用 self._redis.get + json.loads")

    def set(self, key: str, value: Any) -> None:
        raise NotImplementedError("RedisBackend Phase 2")

    def incr(self, key: str, delta: float = 1.0) -> float:
        raise NotImplementedError("RedisBackend Phase 2: 用 self._redis.incrbyfloat")

    def list_append(self, key: str, item: Any, maxlen: Optional[int] = None) -> None:
        raise NotImplementedError("RedisBackend Phase 2: rpush + ltrim")

    def list_get(self, key: str, limit: Optional[int] = None) -> List[Any]:
        raise NotImplementedError("RedisBackend Phase 2: lrange")

    def clear(self, key_prefix: Optional[str] = None) -> None:
        raise NotImplementedError("RedisBackend Phase 2")
