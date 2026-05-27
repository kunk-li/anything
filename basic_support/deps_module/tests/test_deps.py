# -*- coding: utf-8 -*-
"""
deps_module 单元测试
"""

import os
import unittest

from deps_module import BasicDeps, build_basic_deps, StartupError, is_dev_mode


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


if __name__ == "__main__":
    unittest.main()
