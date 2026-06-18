from __future__ import annotations

import logging
import os
import multiprocessing
import threading
from typing import Dict, Optional

from .base import BaseLogger
from log_module.config import LogConfig
from log_module.utils import (
    get_log_file_name, get_process_info, ensure_dir,
    JsonFormatter, use_json_format,  # Task UU (#81)
)


# 全局进程锁：用于多进程并发写文件时保证行级别有序输出
PROCESS_LOCK = multiprocessing.Lock()

# 日志格式：包含进程ID、进程名称，便于多进程排查
LOG_FORMAT = "%(asctime)s - %(process)d - %(processName)s - %(name)s - %(levelname)s - %(message)s"


class SystemLogger(BaseLogger):
    """系统日志实现类（支持控制台+文件输出，内置多进程适配）。

    设计要点（与设计文档一致）：
    - 单例：每个进程一个实例（避免跨进程共享同一 logger/handler）
    - 多进程：FileHandler 为“每个进程独立句柄”；写入使用 PROCESS_LOCK 防止错乱
    - 对外：提供 get_logger / debug/info/warning/error/critical 简洁接口
    """

    _instances: Dict[int, "SystemLogger"] = {}

    def __new__(cls, *args, **kwargs):
        pid = os.getpid()
        inst = cls._instances.get(pid)
        if inst is None:
            inst = super().__new__(cls)
            cls._instances[pid] = inst
            inst._initialized = False  # type: ignore[attr-defined]
        return inst

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return

        self._config = LogConfig.load()
        self.log_level = self._to_level(self._config.log_level)
        self.log_dir = self._config.log_dir
        ensure_dir(self.log_dir)

        self._base_logger_name = self._config.logger_name
        self._logger_cache: Dict[str, logging.Logger] = {}
        # 串行化 logger 构建 (cache 读写 + handler 挂载)。RLock: get_logger 持锁会再调 _build_logger。
        # 无锁时两线程并发首次取同名 logger 会各挂一遍 handler → 日志重复行。
        self._build_lock = threading.RLock()

        # 初始化默认 logger（当前进程独立句柄）
        self._default_logger = self._build_logger(self._base_logger_name)

        self._initialized = True

    @staticmethod
    def _to_level(level: str) -> int:
        m = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "WARN": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
            "FATAL": logging.CRITICAL,
        }
        return m.get(str(level).upper(), logging.INFO)

    def _build_logger(self, logger_name: str) -> logging.Logger:
        pid, _pname = get_process_info()
        real_name = f"{logger_name}_{pid}"

        logger = logging.getLogger(real_name)
        logger.setLevel(self.log_level)
        logger.propagate = False

        # 持锁 + double-check 避免重复添加 handler（并发首次取同名 logger 时）
        with self._build_lock:
            if logger.handlers:
                return logger

            # Task UU (#81): env var driven JSON vs plain formatter
            # ANYTHING_LOG_FORMAT=json  → 单行 JSON, 适合 ELK / Datadog / Loki ingest
            # 缺省 / =plain               → 老的 "asctime - pid - name - level - msg" 格式
            if use_json_format():
                formatter: logging.Formatter = JsonFormatter()
            else:
                formatter = logging.Formatter(LOG_FORMAT)

            # 控制台 handler
            ch = logging.StreamHandler()
            ch.setLevel(self.log_level)
            ch.setFormatter(formatter)

            # 文件 handler（每进程独立句柄；同文件名，锁保证安全）
            log_file = os.path.join(self.log_dir, get_log_file_name())
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(self.log_level)
            fh.setFormatter(formatter)

            logger.addHandler(ch)
            logger.addHandler(fh)
        return logger

    def get_logger(self, logger_name: str = "rag_agent_system") -> logging.Logger:
        # 缓存：同进程、同 logger_name 返回同一对象
        if not logger_name:
            logger_name = self._base_logger_name

        lg = self._logger_cache.get(logger_name)
        if lg is None:
            with self._build_lock:
                lg = self._logger_cache.get(logger_name)  # double-check
                if lg is None:
                    lg = self._build_logger(logger_name)
                    self._logger_cache[logger_name] = lg
        return lg

    def _log(self, level: int, message: str, logger_name: str = "rag_agent_system") -> None:
        logger = self.get_logger(logger_name)

        # 写文件前加锁：避免多进程交错输出导致“半行”错乱
        # 说明：logging 的 FileHandler 在多进程时不是进程安全的，因此这里显式加锁。
        with PROCESS_LOCK:
            logger.log(level, message)

    def debug(self, message: str, logger_name: str = "rag_agent_system") -> None:
        self._log(logging.DEBUG, message, logger_name)

    def info(self, message: str, logger_name: str = "rag_agent_system") -> None:
        self._log(logging.INFO, message, logger_name)

    def warning(self, message: str, logger_name: str = "rag_agent_system") -> None:
        self._log(logging.WARNING, message, logger_name)

    def error(self, message: str, logger_name: str = "rag_agent_system") -> None:
        self._log(logging.ERROR, message, logger_name)

    def critical(self, message: str, logger_name: str = "rag_agent_system") -> None:
        self._log(logging.CRITICAL, message, logger_name)
