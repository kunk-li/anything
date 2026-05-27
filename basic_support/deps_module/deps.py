# -*- coding: utf-8 -*-
"""
BasicDeps: 基础支撑层依赖容器

包含 ConfigManager / SystemLogger / CommonUtils / ExceptionHandler 四个基础组件
的单一实例引用。bootstrap 在装配阶段创建一次，向各上层模块注入；
各模块的 __init__ 优先使用注入的 deps，未注入时回退为自行构造（向后兼容）。

为什么不用 frozen=True dataclass?
    部分组件构造时会做"延迟加载配置 / 初始化日志句柄"等副作用，外部代码可能在
    构造后再次调用 load。冻结实例会阻断这种合法使用，因此采用普通 dataclass。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional


class StartupError(RuntimeError):
    """系统启动阶段的关键依赖初始化失败异常。

    与运行时业务异常区分:启动期错误意味着系统不应进入服务态,
    应该在最早的时机暴露,而不是默默回退到占位实现让真实请求才挂。

    使用约定:
    - 配置缺失 / 模块未安装 / 关键组件初始化抛错 → 包装为 StartupError 抛出
    - 在 ANYTHING_DEV_MODE=1 环境下,允许部分组件回退到占位实现以便本地调试
    """

    def __init__(self, component: str, reason: str, hint: Optional[str] = None):
        self.component = component
        self.reason = reason
        self.hint = hint
        msg = f"[startup] {component} 初始化失败: {reason}"
        if hint:
            msg += f" | hint: {hint}"
        super().__init__(msg)


def is_dev_mode() -> bool:
    """是否启用开发模式(允许占位实现 / 静默回退)。

    优先级:
        1. 环境变量 ANYTHING_DEV_MODE in ("1","true","True","yes")
        2. 默认 False(生产/严格模式)
    """
    val = os.environ.get("ANYTHING_DEV_MODE", "")
    return val.lower() in ("1", "true", "yes", "on")


def handle_exception_to_envelope(
    exception_handler: Any,
    exception: BaseException,
    trace_id: Optional[str],
    fallback_code: str,
    fallback_message: str,
    stage: str,
    context: Optional[dict] = None,
    retryable_codes: Optional[set] = None,
) -> dict:
    """把异常统一包装为响应信封 dict (文档第 10 章七字段格式)。

    抽出原 SimpleRAG / SimpleAgent / SimpleOrchestrator / ConsoleApp 中
    重复 ~30 行的 _handle_exception 方法。

    参数:
        exception_handler: ExceptionHandler 实例 (来自 deps.exception_handler)
        exception: 触发的异常对象
        trace_id: 链路追踪 ID,统一原样透传到信封
        fallback_code: 当 exception_handler 不能识别异常码时使用的兜底码
            (如 "RAG_RUN_FAILED" / "AGENT_RUN_FAILED" / "ORCHESTRATOR_RUN_FAILED")
        fallback_message: 兜底 message
        stage: 调用方所在阶段(写入 details.stage),如 "rag" / "agent" / "orchestrator"
        context: 额外的上下文字段,会合并进 details
        retryable_codes: 视为 retryable 的错误码集合 (调用方可定制)

    返回:
        {code, message, data:None, trace_id, retryable, details}
    """
    context = context or {}
    retryable_codes = retryable_codes or set()

    try:
        # 兼容 ExceptionHandler 两种方法名 (handle / handle_exception)
        # 这里保留 hasattr 是因为 exception_module 的 ABC 没钉死接口,
        # 后续可在 Task #14 之外的清理任务中统一为单一方法名。
        if hasattr(exception_handler, "handle"):
            error_info = exception_handler.handle(exception, trace_id=trace_id)
        elif hasattr(exception_handler, "handle_exception"):
            error_info = exception_handler.handle_exception(exception)
        else:
            error_info = {}

        if not isinstance(error_info, dict):
            error_info = {}

        code = error_info.get("code", fallback_code)
        message = error_info.get("message", fallback_message)
        retryable = error_info.get("retryable", code in retryable_codes)
        details = error_info.get("details") or {"stage": stage, **context}

        return {
            "code": code,
            "message": message,
            "data": None,
            "trace_id": trace_id,
            "retryable": retryable,
            "details": details,
        }
    except Exception:
        # exception_handler 本身崩了 — 走最简兜底,确保系统永远有响应信封返回
        return {
            "code": fallback_code,
            "message": fallback_message,
            "data": None,
            "trace_id": trace_id,
            "retryable": False,
            "details": {"stage": stage, **context},
        }


@dataclass
class BasicDeps:
    """基础支撑层依赖容器。

    Fields:
        config: ConfigManager 实例（已 load_config / load）
        logger: SystemLogger 实例
        utils: CommonUtils 实例
        exception_handler: ExceptionHandler 实例
    """

    config: Any
    logger: Any
    utils: Any
    exception_handler: Any


def build_basic_deps() -> BasicDeps:
    """工厂方法：按默认方式构造一套 BasicDeps。

    本方法是 bootstrap.build_basic_support 的功能等价物，但返回结构化容器
    便于直接注入下游模块；bootstrap 应优先调用本方法。

    注意：import 延迟到函数内，避免模块导入期触发 ConfigManager 加载，
    单测时可只 import BasicDeps 而不触发实例化。
    """
    from common_utils_module.core.impl import CommonUtils
    from config_module.core.impl import ConfigManager
    from log_module.core.impl import SystemLogger
    from exception_module.core.impl import ExceptionHandler

    config = ConfigManager()
    # ConfigManager 在历史上同时存在 load_config / load 两种方法名,兼容处理
    if hasattr(config, "load_config"):
        config.load_config()
    elif hasattr(config, "load"):
        config.load()

    return BasicDeps(
        config=config,
        logger=SystemLogger(),
        utils=CommonUtils(),
        exception_handler=ExceptionHandler(),
    )
