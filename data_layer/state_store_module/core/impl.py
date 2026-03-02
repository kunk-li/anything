from __future__ import annotations

import os
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

from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import SystemBaseException


class StateStoreException(SystemBaseException):
    """状态存储模块异常（建议在全局错误码表中补充 STATE_STORE_* 系列编码）。"""
    pass


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
        config_manager: Optional[ConfigManager] = None,
        logger: Optional[SystemLogger] = None,
    ):
        self.config_manager = config_manager or ConfigManager()
        self.logger = logger or SystemLogger()

        # 加载配置
        try:
            cfg: StateStoreConfig = load_state_store_config(self.config_manager)
        except Exception as e:
            # 兼容：配置模块可能尚未准备好，使用默认值
            self.logger.warning(f"state_store配置加载失败，使用默认配置：{e}", logger_name="state_store_module")
            cfg = StateStoreConfig()

        self.store_dir = store_dir or cfg.dir or "state_store"
        self.expire_hours = cfg.expire_hours
        self.max_size = cfg.max_size

        os.makedirs(self.store_dir, exist_ok=True)

        # 启动时做一次过期清理（best-effort）
        try:
            self._cleanup_expired_states()
        except Exception as e:
            self.logger.warning(f"过期状态清理失败（忽略）：{e}", logger_name="state_store_module")

    def _get_state_path(self, session_id: str) -> str:
        validate_session_id(session_id)
        return os.path.join(self.store_dir, f"{session_id}.json")

    def _now_iso(self) -> str:
        # 使用UTC时间，避免跨时区问题
        return datetime.now(timezone.utc).isoformat()

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

    def save_state(self, session_id: str, state: Dict[str, Any]) -> bool:
        try:
            self._cleanup_expired_states()
            self._enforce_capacity()
            path = self._get_state_path(session_id)

            # 规范化：补充元信息
            state_to_save = dict(state) if isinstance(state, dict) else {}
            state_to_save.setdefault("events", [])
            state_to_save["_meta"] = {
                "session_id": session_id,
                "updated_at": self._now_iso(),
            }

            safe_atomic_write_json(path, state_to_save)
            self.logger.info(f"状态保存成功：{session_id}", logger_name="state_store_module")
            return True
        except StateStoreException:
            raise
        except Exception as e:
            self.logger.error(f"状态保存失败：{session_id} - {e}", logger_name="state_store_module")
            raise StateStoreException("STATE_STORE_SAVE_FAILED", f"状态保存失败：{e}") from e

    def get_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        try:
            self._cleanup_expired_states()
            path = self._get_state_path(session_id)
            if not os.path.exists(path):
                return None
            data = safe_read_json(path)
            # 若读取到的结构不完整，做轻量修复（不落盘）
            if isinstance(data, dict):
                data.setdefault("events", [])
            return data
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

            # 若会话不存在，则创建基础状态
            state = self.get_state(session_id)
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

    def clear_state(self, session_id: str) -> bool:
        try:
            path = self._get_state_path(session_id)
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
