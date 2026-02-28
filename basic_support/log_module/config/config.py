from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class LogConfig:
    """日志模块配置。

    优先级（从高到低）：
    1) 环境变量：LOG_LEVEL, LOG_DIR, LOGGER_NAME
    2) 若存在 config_module（基础支撑层配置管理模块），则从其读取
    3) 默认值
    """

    log_level: str = "INFO"
    log_dir: str = "logs"
    logger_name: str = "rag_agent_system"

    @staticmethod
    def _from_env() -> dict[str, Any]:
        return {
            "log_level": os.getenv("LOG_LEVEL"),
            "log_dir": os.getenv("LOG_DIR"),
            "logger_name": os.getenv("LOGGER_NAME"),
        }

    @classmethod
    def load(cls) -> "LogConfig":
        env = cls._from_env()
        cfg = {k: v for k, v in env.items() if v}

        # 尝试从外部 config_module 读取（若项目中存在）
        # 约定：ConfigManager().load_config(); ConfigManager().get_config(key, default)
        try:
            from config_module.core.impl import ConfigManager  # type: ignore
            cm = ConfigManager()
            try:
                cm.load_config()
            except Exception:
                # 不阻断日志模块初始化
                pass

            cfg.setdefault("log_level", cm.get_config("log_level", cls.log_level))
            cfg.setdefault("log_dir", cm.get_config("log_dir", cls.log_dir))
            cfg.setdefault("logger_name", cm.get_config("logger_name", cls.logger_name))
        except Exception:
            # config_module 不存在或不可用，继续使用 env/default
            cfg.setdefault("log_level", cls.log_level)
            cfg.setdefault("log_dir", cls.log_dir)
            cfg.setdefault("logger_name", cls.logger_name)

        return cls(
            log_level=str(cfg["log_level"]),
            log_dir=str(cfg["log_dir"]),
            logger_name=str(cfg["logger_name"]),
        )
