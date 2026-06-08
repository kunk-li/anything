from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Optional, Any

from .base import BaseExceptionHandler
from ..config.config import ExceptionModuleConfig
from ..utils.tool_functions import get_exception_message, exception_code_to_message, format_traceback

# 按日志模块设计文档：外部模块应通过 `from log_module import SystemLogger` 导入
try:
    from log_module import SystemLogger  # type: ignore
except Exception:  # pragma: no cover
    SystemLogger = None  # type: ignore


class _FallbackLogger:
    """当 log_module 不可用时的兜底日志实现（仅用于本模块独立运行与测试）。"""

    def __init__(self, *, level: str = "INFO") -> None:
        import logging

        self._logger = logging.getLogger("exception_module")
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                fmt="%(asctime)s - %(process)d - %(processName)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)

        self._logger.setLevel(getattr(logging, level, logging.INFO))

    def debug(self, message: str, logger_name: str = "exception_module") -> None:
        self._logger.debug(f"[{logger_name}] {message}")

    def info(self, message: str, logger_name: str = "exception_module") -> None:
        self._logger.info(f"[{logger_name}] {message}")

    def warning(self, message: str, logger_name: str = "exception_module") -> None:
        self._logger.warning(f"[{logger_name}] {message}")

    def error(self, message: str, logger_name: str = "exception_module") -> None:
        self._logger.error(f"[{logger_name}] {message}")

    def critical(self, message: str, logger_name: str = "exception_module") -> None:
        self._logger.critical(f"[{logger_name}] {message}")


# 定义系统统一异常基类，所有自定义异常均继承此类，关联系统错误码
class SystemBaseException(Exception):
    """系统基础异常类（统一异常基类）。"""

    def __init__(self, code: str, message: str, *, details: Optional[Dict[str, Any]] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class ConfigException(SystemBaseException):
    """配置相关异常（CONFIG_*）。"""
    pass


class VectorDBException(SystemBaseException):
    """向量数据库相关异常（VECTOR_*）。"""
    pass


class RAGException(SystemBaseException):
    """RAG模块相关异常（RAG_*）。"""
    pass


class AgentException(SystemBaseException):
    """Agent模块相关异常（AGENT_* / TOOL_*）。"""
    pass


class OrchestratorException(SystemBaseException):
    """协同调度模块相关异常（路由/参数校验/执行失败）。

    补齐 per-domain 异常 (此前漏定义, 致 orchestrator_module.utils.tool_functions
    及其测试 `from exception_module.core.impl import OrchestratorException` 失败 →
    全量 pytest collection error)。构造同基类 (code, message, *, details)。"""
    pass


@dataclass(frozen=True)
class ExceptionHandleResult:
    code: str
    message: str


class ExceptionHandler(BaseExceptionHandler):
    """异常处理器实现。

    与 config_module 对齐点（可选）：
    - 支持从 config_module 读取 global.log_level 等全局配置，用于 fallback logger 日志级别
    - 支持读取 exception_module.logger_name 覆盖默认 logger_name（若你们在 config.yaml 中配置）
    配置管理模块文档给出全局键：global.log_level / global.env 等。fileciteturn2file0
    """

    def __init__(
        self,
        config: Optional[ExceptionModuleConfig] = None,
        *,
        config_manager: Optional[Any] = None,
    ):
        # 1) 基础配置：先用传入 config，否则用默认
        base_config = config or ExceptionModuleConfig()

        # 2) 若注入 config_manager（来自 config_module），则尝试覆盖部分字段
        #    - 不强依赖 config_module：仅要求 config_manager 有 get_config(key, default) 方法
        logger_name = base_config.logger_name
        fallback_level = base_config.fallback_log_level

        if config_manager is not None and hasattr(config_manager, "get_config"):
            try:
                # 全局日志级别（文档示例：global.log_level）fileciteturn2file0
                fallback_level = config_manager.get_config("global.log_level", fallback_level)
                # 若你们在 config.yaml 中为本模块单独配置 logger_name，可用 exception_module.logger_name
                logger_name = config_manager.get_config("exception_module.logger_name", logger_name)
            except Exception:
                # 配置读取失败不影响异常处理
                pass

        # 3) 固化为最终 config（冻结 dataclass，创建新对象）
        self.config = ExceptionModuleConfig(
            include_raw_message_for_unknown=base_config.include_raw_message_for_unknown,
            unknown_message_prefix=base_config.unknown_message_prefix,
            handler_error_prefix=base_config.handler_error_prefix,
            logger_name=logger_name,
            fallback_log_level=ExceptionModuleConfig.normalize_log_level(fallback_level),
        )

        # 4) 初始化 logger：优先 log_module；否则 fallback（级别按 config_module/global.log_level 对齐）
        if SystemLogger is not None:
            try:
                self.logger = SystemLogger()  # type: ignore
            except Exception:
                self.logger = _FallbackLogger(level=self.config.fallback_log_level)
        else:
            self.logger = _FallbackLogger(level=self.config.fallback_log_level)

    def get_exception_code(self, exception: Exception) -> str:
        if isinstance(exception, SystemBaseException):
            return exception.code
        return "UNKNOWN_ERROR"

    def _build_message(self, exception: Exception, code: str) -> str:
        if isinstance(exception, SystemBaseException):
            msg = (exception.message or "").strip()
            if msg:
                return msg
            return exception_code_to_message(code, **(exception.details or {}))

        if self.config.include_raw_message_for_unknown:
            raw = get_exception_message(exception)
            return f"{self.config.unknown_message_prefix}{raw}"
        return exception_code_to_message(code)

    def _log(self, *, level: str, payload: Dict[str, Any]) -> None:
        # 以 JSON 单条日志输出，便于 grep/解析
        try:
            msg = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            msg = str(payload)

        logger_name = self.config.logger_name
        if level == "CRITICAL":
            self.logger.critical(msg, logger_name=logger_name)
        elif level == "ERROR":
            self.logger.error(msg, logger_name=logger_name)
        elif level == "WARNING":
            self.logger.warning(msg, logger_name=logger_name)
        elif level == "DEBUG":
            # log_module 支持 debug；fallback 也支持
            if hasattr(self.logger, "debug"):
                self.logger.debug(msg, logger_name=logger_name)
            else:
                self.logger.info(msg, logger_name=logger_name)
        else:
            self.logger.info(msg, logger_name=logger_name)

    def handle_exception(self, exception: Exception) -> Dict[str, str]:
        try:
            code = self.get_exception_code(exception)
            message = self._build_message(exception, code)
            tb = format_traceback(exception)

            self._log(
                level="ERROR" if code != "UNKNOWN_ERROR" else "WARNING",
                payload={
                    "event": "exception_captured",
                    "code": code,
                    "message": message,
                    "exception_type": exception.__class__.__name__,
                    "traceback": tb,
                    "details": getattr(exception, "details", None),
                },
            )

            return {"code": code, "message": message}

        except Exception as handler_exc:
            fallback_code = "EXCEPTION_HANDLER_ERROR"
            handler_msg = get_exception_message(handler_exc)
            message = f"{self.config.handler_error_prefix}{handler_msg}"

            try:
                self._log(
                    level="CRITICAL",
                    payload={
                        "event": "exception_handler_internal_error",
                        "code": fallback_code,
                        "message": message,
                        "original_exception_type": exception.__class__.__name__,
                        "handler_exception_type": handler_exc.__class__.__name__,
                        "handler_traceback": format_traceback(handler_exc),
                    },
                )
            except Exception:
                pass

            return {"code": fallback_code, "message": message}
