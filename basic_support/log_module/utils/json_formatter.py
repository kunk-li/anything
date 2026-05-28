# -*- coding: utf-8 -*-
"""
JsonFormatter — 把 LogRecord 序列化成单行 JSON, 便于 ELK / Datadog /
                Loki 等日志聚合系统直接 ingest.

Task UU (#81): 给 SystemLogger 加 JSON 输出能力, env 切换:
    ANYTHING_LOG_FORMAT=json   → 每行一个 JSON object
    ANYTHING_LOG_FORMAT=plain  → 老的 "%(asctime)s - %(process)d - ..." 格式 (默认)

字段:
    ts          ISO 8601 with timezone (UTC)
    level       INFO / WARNING / ERROR ...
    logger      logger 名 (含进程 pid 后缀, e.g., rag_agent_system_12345)
    pid         os.getpid()
    process     multiprocessing process name
    thread      threading.current_thread().name
    message     原始 log message (用户传的 str)
    exc_info    若 record 带 exception, 这里放 formatted traceback (单字符串)
    + 任意 extras (通过 LoggerAdapter / logger.X(..., extra={'trace_id': ...}) 传入)

context vars (optional):
    若 observability_module.get_current_tenant() / current_trace_id 可用, 自动注入
    tenant_id / trace_id 字段. 缺失时不写, 不报错.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# LogRecord 上由 logging 库自带的字段, 我们不当作 "extra" 输出 (避免重复)
_LOGRECORD_STANDARD_FIELDS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})


class JsonFormatter(logging.Formatter):
    """logging.Formatter 子类, 输出单行 JSON."""

    def __init__(self, *, ensure_ascii: bool = False, default_kwargs: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.ensure_ascii = ensure_ascii
        self.default_kwargs = default_kwargs or {}

    def format(self, record: logging.LogRecord) -> str:
        # 基础字段
        obj: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "pid": record.process,
            "process": record.processName,
            "thread": record.threadName,
            "message": record.getMessage(),
        }

        # 异常信息
        if record.exc_info:
            obj["exc_info"] = self.formatException(record.exc_info)
        elif record.exc_text:
            obj["exc_info"] = record.exc_text

        # extras (通过 logger.X(..., extra={'k': v}) 传入)
        for k, v in record.__dict__.items():
            if k in _LOGRECORD_STANDARD_FIELDS or k.startswith("_"):
                continue
            try:
                # 探测可序列化
                json.dumps(v, default=str)
                obj[k] = v
            except (TypeError, ValueError):
                obj[k] = str(v)

        # 自动注入 observability context (best-effort, 失败静默)
        try:
            from observability_module import get_current_tenant, get_current_trace_id  # type: ignore
            tenant = get_current_tenant() if callable(get_current_tenant) else None
            if tenant:
                obj.setdefault("tenant_id", tenant)
            trace = get_current_trace_id() if callable(get_current_trace_id) else None
            if trace:
                obj.setdefault("trace_id", trace)
        except Exception:
            pass

        # default_kwargs (constant fields, e.g., service_name="anything")
        for k, v in self.default_kwargs.items():
            obj.setdefault(k, v)

        try:
            return json.dumps(obj, ensure_ascii=self.ensure_ascii, default=str)
        except Exception as e:
            # 兜底: 应该不会发生 (default=str 已经处理大多数情况)
            return json.dumps({
                "ts": obj["ts"], "level": "ERROR", "logger": "json_formatter",
                "message": f"format failed: {e}; original_msg={record.getMessage()!r}",
            }, ensure_ascii=False)


def use_json_format() -> bool:
    """env var driven switch — ANYTHING_LOG_FORMAT=json 时开启."""
    return os.environ.get("ANYTHING_LOG_FORMAT", "").lower() in ("json", "1", "true", "yes")
