from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ExceptionModuleConfig:
    """异常处理模块配置。

    设计原则：
    - exception_module 本身不强依赖 config_module / log_module（便于独立开发与单测）
    - 集成到系统时，可通过 config_module 读取全局配置（如 global.log_level）与本模块配置
      并在初始化 ExceptionHandler 时注入（详见 ExceptionHandler 的 config_manager 参数）

    参考配置管理模块文档：
    - 全局配置示例包含 global.log_level / global.env 等配置键。fileciteturn2file0
    """

    # ---- message 输出策略 ----
    # 未知异常时是否在返回 message 中包含原始异常信息
    include_raw_message_for_unknown: bool = True
    # 未知异常前缀（用于统一输出）
    unknown_message_prefix: str = "未知异常："
    # 异常处理模块自身发生错误时的兜底 message 前缀
    handler_error_prefix: str = "异常处理器内部错误："

    # ---- 日志相关（可由 config_module 覆盖）----
    # logger_name：用于 log_module 的 logger_name 字段（便于按模块过滤）
    logger_name: str = "exception_module"

    # fallback logger 的日志级别（当 log_module 不可用时生效）
    # 建议与 config_module 的 global.log_level 对齐，如 DEBUG/INFO/WARNING/ERROR/CRITICAL fileciteturn2file0
    fallback_log_level: str = "INFO"

    @staticmethod
    def normalize_log_level(level: Optional[str]) -> str:
        if not level:
            return "INFO"
        v = str(level).strip().upper()
        if v in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            return v
        return "INFO"
