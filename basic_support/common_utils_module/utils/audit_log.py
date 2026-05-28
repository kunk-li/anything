# -*- coding: utf-8 -*-
"""
Audit log (Task CC #63)

JSONL append-only 审计日志, 通过 hooks 系统注册. 每条记录:
    timestamp_iso  : "2026-05-28T14:30:00+00:00"
    event          : "llm_call" | "tool_call" | "tool_blocked" | "llm_blocked"
    trace_id       : 跨链路关联
    session_id     : 会话 id
    tenant_id      : 租户 id
    model          : LLM 模型名 (llm_call 才有)
    tool           : 工具名 (tool_call 才有)
    prompt_chars   : prompt 长度 (LLM)
    response_chars : response 长度 (LLM)
    prompt_tokens / completion_tokens / cost_usd  (LLM, 来自 UsageTracker)
    input_keys     : 工具入参的 key 列表 (脱敏, 不存值)
    success        : True / False
    error_code     : 失败时的 code (BlockedError.code 等)

文件位置:
  - 默认 run/audit.log.jsonl (相对 cwd)
  - env ANYTHING_AUDIT_LOG_PATH 覆盖
  - 配 max_bytes 滚动 (默认 10MB), 文件名加 .1 / .2 后缀

设计: 完全异步 + 失败静默. audit 不能影响主链路.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .hooks import get_hook_registry


class AuditLogger:
    """JSONL append-only 审计日志写入器. 线程安全, 失败静默."""

    DEFAULT_PATH = "audit.log.jsonl"
    DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
    DEFAULT_BACKUP_COUNT = 3

    def __init__(
        self,
        path: Optional[str] = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
    ):
        self.path = Path(
            path
            or os.environ.get("ANYTHING_AUDIT_LOG_PATH")
            or self.DEFAULT_PATH
        )
        self.max_bytes = max(0, int(max_bytes))
        self.backup_count = max(0, int(backup_count))
        self._lock = threading.Lock()
        self._count = 0  # 进程内累计写入次数 (调试 / snapshot 用)
        try:
            if self.path.parent and str(self.path.parent) not in ("", "."):
                self.path.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError):
            # 路径不合法 (e.g. 含 \x00) 也吞 — audit 不能拖垮主链路
            pass

    def write(self, record: Dict[str, Any]) -> bool:
        """append 一条记录, 返回是否成功. 失败不抛."""
        if not isinstance(record, dict):
            return False
        try:
            line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        except (TypeError, ValueError):
            return False
        with self._lock:
            try:
                self._rotate_if_needed(extra_size=len(line.encode("utf-8")))
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)
                self._count += 1
                return True
            except (OSError, ValueError):
                return False

    def _rotate_if_needed(self, extra_size: int = 0) -> None:
        """如果当前文件 + 新行 > max_bytes, 把 audit.log.jsonl → .1 → .2 → ..."""
        if self.max_bytes <= 0 or not self.path.exists():
            return
        try:
            cur_size = self.path.stat().st_size
        except OSError:
            return
        if cur_size + extra_size <= self.max_bytes:
            return
        # 轮转: .2 → .3, .1 → .2, current → .1
        if self.backup_count > 0:
            for i in range(self.backup_count - 1, 0, -1):
                src = self.path.with_suffix(self.path.suffix + f".{i}")
                dst = self.path.with_suffix(self.path.suffix + f".{i+1}")
                if src.exists():
                    try:
                        if dst.exists():
                            dst.unlink()
                        src.rename(dst)
                    except OSError:
                        pass
            try:
                self.path.rename(self.path.with_suffix(self.path.suffix + ".1"))
            except OSError:
                pass
        else:
            try:
                self.path.unlink()
            except OSError:
                pass

    def snapshot(self) -> Dict[str, Any]:
        """给 /admin/status 看用."""
        try:
            size = self.path.stat().st_size if self.path.exists() else 0
        except OSError:
            size = 0
        return {
            "path": str(self.path),
            "current_size_bytes": size,
            "max_bytes": self.max_bytes,
            "backup_count": self.backup_count,
            "writes_this_process": self._count,
        }


# ============ 单例 + 默认 hook 注册 ============
_default: Optional[AuditLogger] = None
_default_lock = threading.Lock()


def get_audit_logger() -> AuditLogger:
    global _default
    with _default_lock:
        if _default is None:
            _default = AuditLogger()
        return _default


def reset_audit_logger() -> None:
    global _default
    with _default_lock:
        _default = None


def configure_audit_logger(
    path: Optional[str] = None,
    max_bytes: int = AuditLogger.DEFAULT_MAX_BYTES,
    backup_count: int = AuditLogger.DEFAULT_BACKUP_COUNT,
) -> AuditLogger:
    global _default
    with _default_lock:
        _default = AuditLogger(path=path, max_bytes=max_bytes, backup_count=backup_count)
        return _default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def install_audit_hooks(path: Optional[str] = None) -> AuditLogger:
    """一行安装: 注册 pre/post_tool_call + pre/post_llm_call hook + 单例.

    用法 (bootstrap 启动时):
        install_audit_hooks()                            # 默认 run/audit.log.jsonl
        install_audit_hooks(path="/var/log/anything/audit.jsonl")
    """
    logger = configure_audit_logger(path=path)
    reg = get_hook_registry()

    def on_pre_tool(tool_name, input_data, ctx):
        # 仅记入参 keys (脱敏)
        try:
            keys = sorted(list((input_data or {}).keys())) if isinstance(input_data, dict) else []
        except Exception:
            keys = []
        logger.write({
            "timestamp_iso": _now_iso(),
            "event": "tool_call_started",
            "tool": tool_name,
            "input_keys": keys,
            "trace_id": (ctx or {}).get("trace_id"),
            "session_id": (ctx or {}).get("session_id"),
            "tenant_id": (ctx or {}).get("tenant_id"),
            "iteration": (ctx or {}).get("iteration"),
        })

    def on_post_tool(tool_name, input_data, output, ctx):
        success = bool((output or {}).get("success") or (output or {}).get("code") == "SUCCESS")
        logger.write({
            "timestamp_iso": _now_iso(),
            "event": "tool_call_finished",
            "tool": tool_name,
            "success": success,
            "code": (output or {}).get("code"),
            "trace_id": (ctx or {}).get("trace_id"),
            "session_id": (ctx or {}).get("session_id"),
            "tenant_id": (ctx or {}).get("tenant_id"),
            "iteration": (ctx or {}).get("iteration"),
        })

    def on_pre_llm(prompt, model, ctx):
        logger.write({
            "timestamp_iso": _now_iso(),
            "event": "llm_call_started",
            "model": model,
            "prompt_chars": len(prompt or ""),
            "trace_id": (ctx or {}).get("trace_id"),
            "session_id": (ctx or {}).get("session_id"),
            "tenant_id": (ctx or {}).get("tenant_id"),
        })

    def on_post_llm(prompt, model, response, ctx):
        logger.write({
            "timestamp_iso": _now_iso(),
            "event": "llm_call_finished",
            "model": model,
            "response_chars": len(response or ""),
            "cost_usd": (ctx or {}).get("cost_usd"),
            "prompt_tokens": (ctx or {}).get("prompt_tokens"),
            "completion_tokens": (ctx or {}).get("completion_tokens"),
            "trace_id": (ctx or {}).get("trace_id"),
            "session_id": (ctx or {}).get("session_id"),
            "tenant_id": (ctx or {}).get("tenant_id"),
        })

    reg.add_pre_tool_call(on_pre_tool)
    reg.add_post_tool_call(on_post_tool)
    reg.add_pre_llm_call(on_pre_llm)
    reg.add_post_llm_call(on_post_llm)
    return logger
