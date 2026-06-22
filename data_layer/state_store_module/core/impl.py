from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .base import BaseStateStore
from state_store_module.config.config import load_state_store_config, StateStoreConfig
from state_store_module.utils.tool_functions import (
    validate_session_id,
    safe_atomic_write_json,
    safe_read_json,
    get_dir_size_bytes,
    list_session_files,
)

from exception_module.core.impl import SystemBaseException
from deps_module import BasicDeps


class StateStoreException(SystemBaseException):
    """状态存储模块异常（建议在全局错误码表中补充 STATE_STORE_* 系列编码）。"""
    pass


# 固定锁池大小: 同 session_id 哈希到同一把锁(串行化其读-改-写), 不同 session 偶尔同桶(轻微误串,
# 不影响正确性)。固定池避免 per-session 锁字典无界增长 + 创建竞态。
_SESSION_LOCK_POOL_SIZE = 64


class LocalStateStore(BaseStateStore):
    """本地状态存储实现，基于JSON文件存储会话状态，适用于开发/测试环境。

    - 每个 session_id 对应一个 JSON 文件: <store_dir>/<session_id>.json
    - 采用原子写入（写临时文件后 replace）降低写入中断导致的文件损坏风险
    - 支持按 expire_hours 清理过期会话
    - 支持目录容量 max_size 限制（超过时拒绝写入）
    """

    def __init__(
        self,
        store_dir: Optional[str] = None,
        deps: Optional[BasicDeps] = None,
        tenant_id: str = "default",
        # 旧风格兼容入参(已废弃,保留是为了不破坏既有调用)
        config_manager=None,
        logger=None,
    ):
        """
        Args:
            tenant_id: 租户标识 (Task #33 PR3a). 本 PR 仅记录, 路径仍单一.
        """
        # 优先级: 显式 config_manager/logger > deps > 默认 build_basic_deps
        if config_manager is not None or logger is not None:
            from deps_module import build_basic_deps
            fallback = build_basic_deps()
            self.config_manager = config_manager or fallback.config
            self.logger = logger or fallback.logger
        else:
            if deps is None:
                from deps_module import build_basic_deps
                deps = build_basic_deps()
            self.config_manager = deps.config
            self.logger = deps.logger
        self.tenant_id = self._validate_tenant_id(tenant_id)

        # 加载配置
        try:
            cfg: StateStoreConfig = load_state_store_config(self.config_manager)
        except Exception as e:
            # 兼容：配置模块可能尚未准备好，使用默认值
            self.logger.warning(f"state_store配置加载失败，使用默认配置：{e}", logger_name="state_store_module")
            cfg = StateStoreConfig()

        # Task #33 PR3b: 按 tenant_id 切目录
        # base = 显式入参 / yaml; store_dir = base/<tenant_id>/ (实际工作目录)
        self.base_store_dir = store_dir or cfg.dir or "state_store"
        self.store_dir = os.path.join(self.base_store_dir, self.tenant_id)
        self.expire_hours = cfg.expire_hours
        self.max_size = cfg.max_size

        os.makedirs(self.store_dir, exist_ok=True)

        # per-session 写锁池: 串行化同一 session 的 append/save/clear, 防并发 append 读-改-写丢事件。
        # 仅 per-process —— 本地 JSON 后端不为多进程/多 worker 并发设计, 跨进程同一 session 仍会竞争
        # (真要并发请换 SQL/Redis 后端)。
        self._session_lock_pool = [threading.Lock() for _ in range(_SESSION_LOCK_POOL_SIZE)]

        # 启动时做一次过期清理（best-effort）
        try:
            self._cleanup_expired_states()
        except Exception as e:
            self.logger.warning(f"过期状态清理失败（忽略）：{e}", logger_name="state_store_module")

    def _get_state_path(self, session_id: str) -> str:
        """主路径: 子目录 <base>/<tenant_id>/<session_id>.json (写永远走这里)"""
        validate_session_id(session_id)
        return os.path.join(self.store_dir, f"{session_id}.json")

    def _resolve_read_path(self, session_id: str) -> str:
        """读路径: 主路径无文件且 default 租户 -> fallback 老扁平路径"""
        validate_session_id(session_id)
        primary = os.path.join(self.store_dir, f"{session_id}.json")
        if os.path.exists(primary):
            return primary
        if self.tenant_id == "default":
            legacy = os.path.join(self.base_store_dir, f"{session_id}.json")
            if os.path.exists(legacy):
                return legacy
        return primary

    def _now_iso(self) -> str:
        # 使用UTC时间，避免跨时区问题
        return datetime.now(timezone.utc).isoformat()

    def _lock_for(self, session_id: str) -> threading.Lock:
        """取该 session 的写锁 (固定池哈希分桶)。同 session 必同锁 → 串行化其 append/save/clear。"""
        return self._session_lock_pool[hash(session_id) % len(self._session_lock_pool)]

    def _enforce_capacity(self) -> None:
        if self.max_size is None:
            return
        current = get_dir_size_bytes(self.store_dir)
        if current > self.max_size:
            raise StateStoreException(
                "STATE_STORE_CAPACITY_EXCEEDED",
                f"状态存储目录容量已超过限制：{current} > {self.max_size}",
            )

    def _cleanup_expired_states(self) -> None:
        if not self.expire_hours or self.expire_hours <= 0:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(hours=int(self.expire_hours))

        for fp in list_session_files(self.store_dir):
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(fp), tz=timezone.utc)
            except Exception:
                continue
            if mtime < cutoff:
                try:
                    os.remove(fp)
                    self.logger.info(f"清理过期状态文件：{fp}", logger_name="state_store_module")
                except Exception as e:
                    self.logger.warning(f"清理过期状态文件失败：{fp} - {e}", logger_name="state_store_module")

    @staticmethod
    def _validate_tenant_id(tenant_id: str) -> str:
        """字符集白名单校验 (深度防御, 防 path traversal). 见 docs/multi-tenancy-design.md §9.2"""
        import re
        if not isinstance(tenant_id, str) or not re.match(r"^[a-z0-9_-]{3,32}$", tenant_id):
            raise ValueError(
                f"tenant_id 必须是 3-32 位 [a-z0-9_-] 字符, 实际收到: {tenant_id!r}"
            )
        return tenant_id

    def save_state(self, session_id: str, state: Dict[str, Any]) -> bool:
        try:
            self._cleanup_expired_states()
            self._enforce_capacity()
            path = self._get_state_path(session_id)

            # 规范化：补充元信息。非 dict 入参直接抛错(与 append_event 同款), 不再静默吞成 {} 丢数据
            if not isinstance(state, dict):
                raise StateStoreException("STATE_STORE_INVALID_STATE", "会话状态格式非法，必须为Dict")
            state_to_save = dict(state)
            state_to_save.setdefault("events", [])
            state_to_save["_meta"] = {
                "session_id": session_id,
                "updated_at": self._now_iso(),
            }

            # 持 per-session 锁写, 与 append_event 的读-改-写互斥 (整存 vs 追加不交错)
            with self._lock_for(session_id):
                safe_atomic_write_json(path, state_to_save)
            self.logger.info(f"状态保存成功：{session_id}", logger_name="state_store_module")
            return True
        except StateStoreException:
            raise
        except Exception as e:
            self.logger.error(f"状态保存失败：{session_id} - {e}", logger_name="state_store_module")
            raise StateStoreException("STATE_STORE_SAVE_FAILED", f"状态保存失败：{e}") from e

    def _read_state_unlocked(self, session_id: str) -> Optional[Dict[str, Any]]:
        """get_state 的无锁实现体。调用方负责持有 _lock_for(session_id):
        get_state (公共入口) 自己加锁; append_event 已在锁内, 直接调本方法避免
        非可重入锁自死锁。"""
        # 读优先走主路径,缺则 default 租户 fallback 老扁平路径
        path = self._resolve_read_path(session_id)
        if not os.path.exists(path):
            return None
        data = safe_read_json(path)
        # 若读取到的结构不完整，做轻量修复（不落盘）
        if isinstance(data, dict):
            data.setdefault("events", [])
        return data

    def get_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        try:
            self._cleanup_expired_states()
            # 持 per-session 锁读: 与写方 (save/append/clear 的 os.replace/remove) 串行化。
            # 否则在 Windows 上本读句柄 (无 FILE_SHARE_DELETE) 会令并发写方的 os.replace
            # 抛 PermissionError → 静默丢事件/历史。readers↔writers 串行是与本提交其余
            # 部分一致的根因修法。
            with self._lock_for(session_id):
                return self._read_state_unlocked(session_id)
        except ValueError:
            # session_id 校验失败
            raise
        except Exception as e:
            self.logger.error(f"状态读取失败：{session_id} - {e}", logger_name="state_store_module")
            return None

    def append_event(self, session_id: str, event: Dict[str, Any]) -> bool:
        try:
            self._cleanup_expired_states()
            self._enforce_capacity()

            if not isinstance(event, dict) or not event:
                raise ValueError("event必须为非空Dict")
            event_to_add = dict(event)
            event_to_add.setdefault("timestamp", self._now_iso())

            # get→append→write 整段必须串行 (持 per-session 锁), 否则两并发 append 各读旧 state、
            # 各写回 → 后写覆盖先写丢事件 (lost update)。
            with self._lock_for(session_id):
                # 已持锁, 走无锁读体: get_state 自身会再取同一把(非可重入)锁 → 自死锁。
                # 若会话不存在，则创建基础状态
                state = self._read_state_unlocked(session_id)
                if state is None:
                    state = {"events": []}

                if not isinstance(state, dict):
                    raise StateStoreException("STATE_STORE_INVALID_STATE", "会话状态格式非法，必须为Dict")

                events = state.get("events")
                if events is None:
                    events = []
                    state["events"] = events
                if not isinstance(events, list):
                    raise StateStoreException("STATE_STORE_INVALID_EVENTS", "会话events字段必须为List")

                events.append(event_to_add)
                # 更新元信息
                meta = state.get("_meta") if isinstance(state.get("_meta"), dict) else {}
                meta.update({"session_id": session_id, "updated_at": self._now_iso()})
                state["_meta"] = meta

                path = self._get_state_path(session_id)
                safe_atomic_write_json(path, state)

            self.logger.info(f"事件追加成功：{session_id}", logger_name="state_store_module")
            return True
        except StateStoreException:
            raise
        except Exception as e:
            self.logger.error(f"事件追加失败：{session_id} - {e}", logger_name="state_store_module")
            raise StateStoreException("STATE_STORE_APPEND_FAILED", f"事件追加失败：{e}") from e

    def merge_state(self, session_id: str, patch: Dict[str, Any]) -> bool:
        """锁内浅合并顶层标量字段, 保留既有 events (race-free 标量更新)。

        与 save_state 的关键区别: save_state 是 full-replace (会把 events 整列覆盖),
        merge_state 只把 patch 里的 key 覆盖进现有 state, 绝不触碰 events ——
        让 agent 层"追加对话事件 (append_event) + 更新顶层标量 (merge_state)"
        两条路径在同一把 per-session 锁下互斥且互不覆盖, 消除 get+save 读-改-写的
        lost-update 竞态 (见 agent_module state_history._save_state_safe)。

        patch 里若误带 events 会被忽略 (events 只能通过 append_event 改)。
        会话不存在则以 patch 为基础创建。
        """
        try:
            self._cleanup_expired_states()
            self._enforce_capacity()

            if not isinstance(patch, dict):
                raise StateStoreException("STATE_STORE_INVALID_STATE", "merge patch 必须为Dict")

            # get→merge→write 整段持 per-session 锁, 与并发 append_event/save_state 互斥
            with self._lock_for(session_id):
                # 已持锁, 走无锁读体: get_state 自身会再取同一把(非可重入)锁 → 自死锁
                state = self._read_state_unlocked(session_id)
                if state is None or not isinstance(state, dict):
                    state = {"events": []}

                existing_events = state.get("events")
                if not isinstance(existing_events, list):
                    existing_events = []

                for k, v in patch.items():
                    if k == "events":
                        # events 只能经 append_event 改, merge 不接管 (防覆盖历史链)
                        continue
                    state[k] = v
                state["events"] = existing_events

                meta = state.get("_meta") if isinstance(state.get("_meta"), dict) else {}
                meta.update({"session_id": session_id, "updated_at": self._now_iso()})
                state["_meta"] = meta

                path = self._get_state_path(session_id)
                safe_atomic_write_json(path, state)

            self.logger.info(f"状态合并成功：{session_id}", logger_name="state_store_module")
            return True
        except StateStoreException:
            raise
        except Exception as e:
            self.logger.error(f"状态合并失败：{session_id} - {e}", logger_name="state_store_module")
            raise StateStoreException("STATE_STORE_MERGE_FAILED", f"状态合并失败：{e}") from e

    def clear_state(self, session_id: str) -> bool:
        try:
            path = self._get_state_path(session_id)
            # 持 per-session 锁删, 与并发 append/save 互斥
            with self._lock_for(session_id):
                if not os.path.exists(path):
                    return True
                os.remove(path)
            self.logger.info(f"状态清理成功：{session_id}", logger_name="state_store_module")
            return True
        except StateStoreException:
            raise
        except Exception as e:
            self.logger.error(f"状态清理失败：{session_id} - {e}", logger_name="state_store_module")
            raise StateStoreException("STATE_STORE_CLEAR_FAILED", f"状态清理失败：{e}") from e

    def list_sessions(self, limit: int = 100, cursor: Optional[str] = None) -> list:
        """Task SSS (#105): 扫 store_dir 列已知 session.

        返回 [{session_id, last_modified, size_bytes, has_history}, ...],
        按 (last_modified, session_id) 双键倒序 (双键保证 mtime 相同时顺序稳定).
        has_history 看 state.get("history") 是否非空.

        cursor 分页: cursor = 上一页最后一条的 "last_modified:session_id",
        传入后只返回排序上严格在其后的条目 — 频繁创删 session 时不会像
        offset 分页那样重复/漏条。非法 cursor 忽略 (等价首页)。
        """
        cur_key = None
        if cursor:
            try:
                mt_s, sid_s = str(cursor).split(":", 1)
                cur_key = (float(mt_s), sid_s)
            except (ValueError, TypeError):
                cur_key = None
        out = []
        try:
            if not os.path.isdir(self.store_dir):
                return out
            entries = []
            for name in os.listdir(self.store_dir):
                if not name.endswith(".json"):
                    continue
                # 跳过原子写产生的临时文件 (.tmp_state_*.json) 及其它点前缀文件,
                # 否则会被当成假 session 列出/peek。
                if name.startswith("."):
                    continue
                p = os.path.join(self.store_dir, name)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                session_id = name[:-5]  # strip .json
                entries.append((st.st_mtime, st.st_size, session_id, p))
            entries.sort(key=lambda x: (x[0], x[2]), reverse=True)
            if cur_key is not None:
                entries = [e for e in entries if (e[0], e[2]) < cur_key]
            for mtime, size, sid, p in entries[:limit]:
                has_history = False
                title = None  # Task YYYY-E (#116): 从首条 user msg 提取
                # 尝试 peek state 看 history 是否非空 (不读完整文件浪费 IO 还是要读)
                try:
                    import json as _json
                    with open(p, "r", encoding="utf-8") as f:
                        st = _json.load(f)
                    if isinstance(st, dict):
                        events = st.get("events") or st.get("history") or []
                        has_history = bool(events)
                        # YYYY-E: 优先级:
                        #   1. state.title (显式存的)
                        #   2. state.task (React agent 顶层 task 字段)
                        #   3. events 里 react_started.payload.task
                        #   4. events 里 role=user 的 content
                        title = st.get("title") or st.get("task")
                        if not title and events:
                            for ev in events:
                                if not isinstance(ev, dict):
                                    continue
                                # React agent: event_type=react_started, payload.task
                                if ev.get("event_type") == "react_started":
                                    pl = ev.get("payload") or {}
                                    cand = pl.get("task")
                                    if cand:
                                        title = str(cand)
                                        break
                                # RAG 对话: role=user, content
                                if ev.get("role") == "user":
                                    cand = (ev.get("content")
                                            or ev.get("query")
                                            or ev.get("user_input")
                                            or "")
                                    if cand:
                                        title = str(cand)
                                        break
                        if title:
                            # Task PPPP: state.task 可能是 Agent 注入了长期记忆的
                            # augmented prompt (开头 "[长期记忆 — 已知...]" + ... +
                            # "[当前任务]" + 原 task). 取原 task 当 title.
                            title_str = str(title)
                            cur_idx = title_str.find("[当前任务]")
                            if cur_idx >= 0:
                                title_str = title_str[cur_idx + len("[当前任务]"):].lstrip()
                            title = title_str.strip().replace("\n", " ")[:30]
                except Exception:
                    pass
                out.append({
                    "session_id": sid,
                    "last_modified": mtime,
                    "size_bytes": size,
                    "has_history": has_history,
                    "title": title,
                })
        except Exception as e:
            self.logger.warning(
                f"list_sessions 扫描失败 (返回部分): {e}",
                logger_name="state_store_module",
            )
        return out
