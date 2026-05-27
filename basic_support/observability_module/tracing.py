# -*- coding: utf-8 -*-
"""
OpenTelemetry 追踪封装(可选依赖)

未安装 opentelemetry-api/sdk 时 trace_span / get_tracer 退化为 no-op,
让业务代码 `with trace_span("xxx"): ...` 永远安全。
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

try:
    from opentelemetry import trace as _otel_trace
    _OTEL_AVAILABLE = True
except ImportError:
    _otel_trace = None  # type: ignore
    _OTEL_AVAILABLE = False


_tracer_provider_initialized = False


# ===========================================================================
# Tenant ContextVar (Task #33 PR4a, 见 docs/multi-tenancy-design.md §7.3.1)
# ===========================================================================
# 每请求一个 Context, ASGI 框架(FastAPI)天然隔离;
# 业务侧只需用 set_current_tenant / reset_current_tenant 配对调用即可。

_current_tenant: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "anything_tenant_id", default=None
)


def set_current_tenant(tenant_id: Optional[str]) -> "contextvars.Token[Optional[str]]":
    """设置当前请求的 tenant_id, 返回 token 用于 reset.

    用法 (ApiService 推荐写法):
        token = set_current_tenant(auth_tenant_id)
        try:
            ...
        finally:
            reset_current_tenant(token)
    """
    return _current_tenant.set(tenant_id)


def reset_current_tenant(token: "contextvars.Token[Optional[str]]") -> None:
    """重置 tenant ContextVar 到之前的状态. 必须与 set 配对使用."""
    _current_tenant.reset(token)


def get_current_tenant() -> Optional[str]:
    """读取当前 ContextVar 中的 tenant_id (没设过时返回 None)."""
    return _current_tenant.get()


def is_otel_available() -> bool:
    """检测 opentelemetry-api 是否可 import."""
    return _OTEL_AVAILABLE


# ===========================================================================
# Tracer Provider 初始化
# ===========================================================================


def setup_tracer_provider(
    service_name: str = "anything",
    exporter: str = "console",
    otlp_endpoint: Optional[str] = None,
) -> bool:
    """一次性初始化 TracerProvider, 重复调用是 no-op。

    Args:
        service_name: 服务名(出现在 trace 的 resource attribute)
        exporter: "console" (开发) | "otlp" (生产, 推送到 collector)
        otlp_endpoint: 当 exporter="otlp" 时, 显式指定 endpoint;
                       为 None 则读环境变量 OTEL_EXPORTER_OTLP_ENDPOINT

    返回:
        True 表示已成功(或之前已)初始化; False 表示 OTel 未安装/初始化失败.
    """
    global _tracer_provider_initialized
    if not _OTEL_AVAILABLE:
        return False
    if _tracer_provider_initialized:
        return True

    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        if exporter == "console":
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        elif exporter == "otlp":
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                if otlp_endpoint:
                    provider.add_span_processor(
                        BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
                    )
                else:
                    # 读环境变量 OTEL_EXPORTER_OTLP_ENDPOINT
                    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            except ImportError:
                # OTLP exporter 未安装, 退化为 console
                from opentelemetry.sdk.trace.export import ConsoleSpanExporter
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        _otel_trace.set_tracer_provider(provider)
        _tracer_provider_initialized = True
        return True
    except Exception:
        return False


def _reset_for_tests() -> None:
    """测试用:重置 initialized 标志位, 让多次 setup 都重新走一遍."""
    global _tracer_provider_initialized
    _tracer_provider_initialized = False


# ===========================================================================
# No-op 实现(OTel 未安装或未初始化时使用)
# ===========================================================================


class _NoopSpan:
    """OTel 不可用时的 span 占位, 实现常用 span 方法但全部 no-op."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_attributes(self, attributes: Dict[str, Any]) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def record_exception(self, exception: BaseException, **kwargs: Any) -> None:
        pass

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        pass

    def end(self) -> None:
        pass


class _NoopTracer:
    """OTel 不可用时的 tracer 占位."""

    @contextmanager
    def start_as_current_span(self, name: str, **kwargs: Any) -> Iterator[_NoopSpan]:
        yield _NoopSpan()


_NOOP_TRACER = _NoopTracer()


# ===========================================================================
# 对外入口
# ===========================================================================


def get_tracer(name: str = "anything"):
    """获取 tracer. OTel 未装或未 setup 时返回 no-op tracer."""
    if not _OTEL_AVAILABLE or not _tracer_provider_initialized:
        return _NOOP_TRACER
    return _otel_trace.get_tracer(name)


@contextmanager
def trace_span(
    name: str,
    tracer: Any = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> Iterator[Any]:
    """便捷封装: 业务代码用 `with trace_span("rag.retrieve"): ...` 包裹关键边界.

    OTel 未装或未 setup 时退化为 no-op context manager, 业务无感知.

    用法:
        with trace_span("api.invoke", attributes={"http.method": "POST"}) as span:
            ...
            span.set_attribute("response.code", "SUCCESS")
    """
    actual_tracer = tracer if tracer is not None else get_tracer()
    # 自动从 ContextVar 注入 tenant_id (调用方未显式传时), 见 §7.3.1
    merged_attrs: Dict[str, Any] = dict(attributes or {})
    current_tid = _current_tenant.get()
    if current_tid is not None and "anything.tenant_id" not in merged_attrs:
        merged_attrs["anything.tenant_id"] = current_tid

    with actual_tracer.start_as_current_span(name) as span:
        if merged_attrs:
            # NoopSpan 也实现了 set_attributes; OTel Span 同样
            try:
                span.set_attributes(merged_attrs)
            except Exception:
                # 极端情况 attribute value 不可序列化等
                pass
        yield span
