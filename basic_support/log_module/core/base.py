from __future__ import annotations

from abc import ABC, abstractmethod
import logging


class BaseLogger(ABC):
    """日志模块抽象基类（ABC）。

    - 定义统一的日志记录接口
    - 多进程适配由实现类封装，对外部调用透明
    """

    @abstractmethod
    def get_logger(self, logger_name: str = "rag_agent_system") -> logging.Logger:
        """获取日志器实例。

        多进程场景下应返回当前进程的独立日志器实例。
        """
        raise NotImplementedError

    @abstractmethod
    def debug(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """记录 DEBUG 级别日志。"""
        raise NotImplementedError

    @abstractmethod
    def info(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """记录 INFO 级别日志。"""
        raise NotImplementedError

    @abstractmethod
    def warning(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """记录 WARNING 级别日志。"""
        raise NotImplementedError

    @abstractmethod
    def error(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """记录 ERROR 级别日志。"""
        raise NotImplementedError

    @abstractmethod
    def critical(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """记录 CRITICAL 级别日志。"""
        raise NotImplementedError
