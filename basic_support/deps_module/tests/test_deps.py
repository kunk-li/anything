# -*- coding: utf-8 -*-
"""
deps_module 单元测试
"""

import os
import unittest

from deps_module import (
    BasicDeps,
    build_basic_deps,
    StartupError,
    is_dev_mode,
    handle_exception_to_envelope,
)


class TestBasicDeps(unittest.TestCase):
    def test_build_basic_deps_returns_populated_container(self):
        deps = build_basic_deps()
        self.assertIsNotNone(deps.config)
        self.assertIsNotNone(deps.logger)
        self.assertIsNotNone(deps.utils)
        self.assertIsNotNone(deps.exception_handler)

    def test_build_basic_deps_returns_new_instance_each_call(self):
        """语义上 BasicDeps 是 bootstrap 阶段构造一次的容器,
        但工厂方法本身允许多次调用(用于测试场景)。"""
        d1 = build_basic_deps()
        d2 = build_basic_deps()
        self.assertIsNot(d1, d2)

    def test_basic_deps_is_mutable_dataclass(self):
        """非 frozen,允许后期替换某个组件(测试场景中替换 mock)。"""
        deps = build_basic_deps()
        deps.logger = "mock_logger"
        self.assertEqual(deps.logger, "mock_logger")


class TestStartupError(unittest.TestCase):
    def test_startup_error_carries_component_and_reason(self):
        err = StartupError(component="vector_db", reason="faiss missing")
        self.assertEqual(err.component, "vector_db")
        self.assertEqual(err.reason, "faiss missing")
        self.assertIn("vector_db", str(err))
        self.assertIn("faiss missing", str(err))

    def test_startup_error_with_hint(self):
        err = StartupError(component="llm", reason="401", hint="set ANYTHING_DEV_MODE=1")
        self.assertIn("set ANYTHING_DEV_MODE=1", str(err))

    def test_startup_error_is_runtime_error_subclass(self):
        """生产代码可以用 except RuntimeError 通用拦截"""
        with self.assertRaises(RuntimeError):
            raise StartupError("x", "y")


class TestIsDevMode(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("ANYTHING_DEV_MODE", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["ANYTHING_DEV_MODE"] = self._saved
        else:
            os.environ.pop("ANYTHING_DEV_MODE", None)

    def test_not_set_returns_false(self):
        self.assertFalse(is_dev_mode())

    def test_truthy_values(self):
        for val in ("1", "true", "True", "TRUE", "yes", "on"):
            os.environ["ANYTHING_DEV_MODE"] = val
            self.assertTrue(is_dev_mode(), f"expect True for {val!r}")

    def test_falsy_values(self):
        for val in ("0", "false", "no", "off", ""):
            os.environ["ANYTHING_DEV_MODE"] = val
            self.assertFalse(is_dev_mode(), f"expect False for {val!r}")


class _StubExceptionHandler:
    """模拟 ExceptionHandler.handle 返回结构化错误码"""
    def __init__(self, info):
        self._info = info
        self.calls = []

    def handle(self, exc, trace_id=None):
        self.calls.append((exc, trace_id))
        return self._info


class _BrokenExceptionHandler:
    """模拟 handle 自身抛异常 -> envelope 应走最简兜底"""
    def handle(self, exc, trace_id=None):
        raise RuntimeError("handler itself crashed")


class TestHandleExceptionToEnvelope(unittest.TestCase):

    def test_handler_provides_full_error_info(self):
        handler = _StubExceptionHandler({
            "code": "VECTOR_QUERY_FAILED",
            "message": "向量检索失败",
            "retryable": True,
            "details": {"index": "faiss_default", "operation": "query"},
        })
        env = handle_exception_to_envelope(
            exception_handler=handler,
            exception=RuntimeError("x"),
            trace_id="t1",
            fallback_code="RAG_RUN_FAILED",
            fallback_message="RAG执行失败",
            stage="rag",
        )
        self.assertEqual(env["code"], "VECTOR_QUERY_FAILED")
        self.assertEqual(env["message"], "向量检索失败")
        self.assertTrue(env["retryable"])
        self.assertEqual(env["details"]["index"], "faiss_default")
        self.assertEqual(env["trace_id"], "t1")
        self.assertIsNone(env["data"])

    def test_handler_returns_partial_info_uses_fallback_code(self):
        handler = _StubExceptionHandler({})  # 空 dict
        env = handle_exception_to_envelope(
            exception_handler=handler,
            exception=RuntimeError("x"),
            trace_id="t1",
            fallback_code="RAG_RUN_FAILED",
            fallback_message="RAG执行失败",
            stage="rag",
            context={"query": "abc"},
            retryable_codes={"RAG_RUN_FAILED"},
        )
        self.assertEqual(env["code"], "RAG_RUN_FAILED")
        self.assertEqual(env["message"], "RAG执行失败")
        # 由于 fallback_code 在 retryable_codes 集合内, retryable=True
        self.assertTrue(env["retryable"])
        self.assertEqual(env["details"]["stage"], "rag")
        self.assertEqual(env["details"]["query"], "abc")

    def test_handler_itself_crashes_returns_minimal_envelope(self):
        env = handle_exception_to_envelope(
            exception_handler=_BrokenExceptionHandler(),
            exception=RuntimeError("real error"),
            trace_id="t1",
            fallback_code="RAG_RUN_FAILED",
            fallback_message="RAG执行失败",
            stage="rag",
            context={"query": "abc"},
        )
        self.assertEqual(env["code"], "RAG_RUN_FAILED")
        self.assertFalse(env["retryable"])
        self.assertEqual(env["details"]["stage"], "rag")
        self.assertEqual(env["details"]["query"], "abc")
        self.assertEqual(env["trace_id"], "t1")

    def test_envelope_has_seven_canonical_fields(self):
        """统一信封必须包含文档第 10 章规定的 7 字段(本工具负责 6 个,
        cost_time 由调用方在外层封装时补)"""
        handler = _StubExceptionHandler({"code": "X", "message": "y"})
        env = handle_exception_to_envelope(
            exception_handler=handler,
            exception=RuntimeError("e"),
            trace_id="t1",
            fallback_code="X",
            fallback_message="y",
            stage="test",
        )
        for field in ("code", "message", "data", "trace_id", "retryable", "details"):
            self.assertIn(field, env, f"信封缺字段: {field}")


if __name__ == "__main__":
    unittest.main()
