from __future__ import annotations

from datetime import datetime
import os
import multiprocessing
from typing import Tuple


def get_log_file_name(prefix: str = "system") -> str:
    """生成按日期命名的日志文件名，格式：{prefix}_YYYYMMDD.log"""
    date_str = datetime.now().strftime("%Y%m%d")
    return f"{prefix}_{date_str}.log"


def format_log_message(message: str, module_name: str) -> str:
    """格式化日志信息，添加模块标识等辅助信息。"""
    return f"[{module_name}] {message}"


def get_process_info() -> Tuple[int, str]:
    """获取当前进程ID和进程名称，用于多进程日志标识。"""
    proc = multiprocessing.current_process()
    return proc.pid, proc.name


def ensure_dir(path: str) -> None:
    """确保目录存在，不存在则创建。"""
    os.makedirs(path, exist_ok=True)
