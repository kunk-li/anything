# -*- coding: utf-8 -*-
"""
deps_module 单元测试
"""

import unittest

from deps_module import BasicDeps, build_basic_deps


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


if __name__ == "__main__":
    unittest.main()
