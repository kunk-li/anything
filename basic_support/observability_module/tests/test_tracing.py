# -*- coding: utf-8 -*-
"""
observability_module.tracing 单元测试

主要覆盖 no-op 路径 (OTel 即使没装也不能让业务挂掉).
真实 OTel 路径在 OTel 已安装的环境下也跑(本地 conda env 可能未装)。
"""

import unittest

from observability_module import (
    is_otel_available,
    get_tracer,
    trace_span,
    setup_tracer_provider,
)
from observability_module.tracing import _NoopTracer, _NoopSpan, _reset_for_tests


class TestNoopPath(unittest.TestCase):
    """无论 OTel 是否可用, no-op 都必须工作"""

    def test_noop_tracer_span_methods(self):
        """NoopSpan 的所有方法都应可调用且不报错"""
        s = _NoopSpan()
        s.set_attribute("k", "v")
        s.set_attributes({"a": 1, "b": "x"})
        s.set_status("ok")
        s.record_exception(RuntimeError("test"))
        s.add_event("event", {"k": "v"})
        s.end()
        # 不应抛任何异常

    def test_noop_tracer_yields_noop_span(self):
        t = _NoopTracer()
        with t.start_as_current_span("test") as span:
            self.assertIsInstance(span, _NoopSpan)


class TestTraceSpan(unittest.TestCase):
    """trace_span context manager 在所有情况下都应安全"""

    def setUp(self):
        # 重置 provider 状态, 让每个测试独立
        _reset_for_tests()

    def test_trace_span_works_without_setup(self):
        """没调 setup_tracer_provider 时 trace_span 退化 no-op"""
        with trace_span("test.span") as span:
            span.set_attribute("k", "v")
            span.set_attributes({"a": 1})
            # 不应抛

    def test_trace_span_with_attributes(self):
        """attributes 参数应被传递给 span"""
        with trace_span("test.span", attributes={"foo": "bar"}) as span:
            # 验证 span 类型 (no-op 或 OTel) 都接受 set_attributes
            self.assertTrue(hasattr(span, "set_attributes"))

    def test_trace_span_nested(self):
        """嵌套 span 应能正常工作"""
        with trace_span("outer") as outer_span:
            with trace_span("inner") as inner_span:
                outer_span.set_attribute("level", "outer")
                inner_span.set_attribute("level", "inner")

    def test_trace_span_exception_does_not_swallow(self):
        """span 包裹内的异常应该正常抛, 不被吞掉"""
        with self.assertRaises(ValueError):
            with trace_span("test"):
                raise ValueError("oops")


class TestSetupTracerProvider(unittest.TestCase):

    def setUp(self):
        _reset_for_tests()

    def test_setup_returns_false_if_otel_not_available(self):
        """OTel 未装时 setup 返回 False, 不抛"""
        if is_otel_available():
            self.skipTest("OTel 已安装, 跳过 not_available 测试")
        self.assertFalse(setup_tracer_provider())

    def test_setup_returns_true_with_otel_console(self):
        """OTel 已装且配 console exporter 时应成功"""
        if not is_otel_available():
            self.skipTest("OTel 未安装")
        result = setup_tracer_provider(service_name="test", exporter="console")
        self.assertTrue(result)

    def test_setup_idempotent(self):
        """重复调 setup 是 no-op (不重新创建 provider)"""
        if not is_otel_available():
            self.skipTest("OTel 未安装")
        setup_tracer_provider(service_name="test", exporter="console")
        # 第二次调用应该直接返回 True 而不报错
        self.assertTrue(setup_tracer_provider(service_name="test", exporter="console"))


class TestGetTracer(unittest.TestCase):

    def setUp(self):
        _reset_for_tests()

    def test_get_tracer_without_setup_returns_noop(self):
        """没 setup 时返回 no-op tracer"""
        t = get_tracer("test")
        self.assertIsInstance(t, _NoopTracer)

    def test_get_tracer_after_setup_returns_real_or_noop(self):
        """setup 后如果 OTel 可用则返回真实 tracer, 否则仍 no-op"""
        setup_tracer_provider(service_name="test", exporter="console")
        t = get_tracer("test")
        if is_otel_available():
            self.assertNotIsInstance(t, _NoopTracer)
        else:
            self.assertIsInstance(t, _NoopTracer)


if __name__ == "__main__":
    unittest.main()
