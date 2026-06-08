# -*- coding: utf-8 -*-
"""
observability_module: 分布式追踪 + 可观察性辅助

提供:
    - trace_span(name, attributes): 给关键调用边界打 OpenTelemetry span
    - get_tracer(name): 获取 tracer
    - setup_tracer_provider(...): 一次性初始化 OTel SDK
    - is_otel_available(): 检测 opentelemetry 是否可 import

设计原则:
    - opentelemetry-api / sdk 是**可选依赖**, 未安装时全部 API 退化为 no-op
    - 任何业务代码用 `with trace_span(...)` 都安全, 不依赖 OTel 也能跑
    - 默认 no-op, 调 setup_tracer_provider() 才真正启用追踪
    - 现有 trace_id 机制(X-Request-Id)保留, 跟 OTel trace_id 是两套但可关联

启用步骤 (生产):
    pip install opentelemetry-api opentelemetry-sdk \
                opentelemetry-exporter-otlp-proto-http
    # 在 bootstrap 启动时:
    from observability_module import setup_tracer_provider
    setup_tracer_provider(service_name="anything", exporter="otlp")
    # 配 OTLP endpoint:
    export OTEL_EXPORTER_OTLP_ENDPOINT=http://your-collector:4318

设计偏离声明:
    同 schema_module / deps_module / chunker_module, 不采用
    core/base.py + impl.py 二分.
"""

from .tracing import (
    is_otel_available,
    get_tracer,
    setup_tracer_provider,
    trace_span,
    set_current_tenant,
    reset_current_tenant,
    get_current_tenant,
    set_model_routing,
    get_model_routing,
    reset_model_routing,
)
# Task Y (#59): token / cost 跟踪
from .usage_tracker import (
    UsageTracker,
    get_usage_tracker,
    reset_usage_tracker,
)

__all__ = [
    "is_otel_available",
    "get_tracer",
    "setup_tracer_provider",
    "trace_span",
    "set_current_tenant",
    "reset_current_tenant",
    "get_current_tenant",
    "set_model_routing",
    "get_model_routing",
    "reset_model_routing",
    "UsageTracker",
    "get_usage_tracker",
    "reset_usage_tracker",
]
