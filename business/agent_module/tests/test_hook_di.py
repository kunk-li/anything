# -*- coding: utf-8 -*-
"""
ReActEngineMixin._hook_registry() DI 验证测试 (Task YY #85).

证明 PP (#76) 的 deps.hook_registry DI 真的能从 react_engine 调到, 不只是
摆设. 重点是 hook 隔离: 注入的 registry !== 全局单例, 注入的 hook 在调用
时被触发, 全局单例上的 hook 不会被触发.
"""
import os
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import unittest
from unittest.mock import MagicMock

from agent_module import SimpleAgent
from deps_module import build_basic_deps
from hooks_module import HookRegistry, get_hook_registry, reset_hook_registry


class TestHookRegistryDI(unittest.TestCase):
    """SimpleAgent._hook_registry() 优先返回 deps.hook_registry, 不去命中全局单例."""

    def setUp(self):
        # 干净状态: 全局单例清空
        reset_hook_registry()

    def tearDown(self):
        reset_hook_registry()

    def test_default_deps_uses_global_singleton(self):
        """没动 deps 时, _hook_registry() 返回的 == get_hook_registry() (全局)."""
        agent = SimpleAgent(tool_registry=None)
        # 默认 build_basic_deps() 已经把 hook_registry 设成全局单例 (PP 注入)
        self.assertIs(agent._hook_registry(), get_hook_registry())

    def test_injected_registry_overrides_global(self):
        """deps.hook_registry 注入自定义 HookRegistry → _hook_registry() 返回它,
        不会去命中 get_hook_registry()."""
        custom_reg = HookRegistry()
        deps = build_basic_deps()
        deps.hook_registry = custom_reg  # 替换 (mutable dataclass)

        agent = SimpleAgent(tool_registry=None, deps=deps)
        self.assertIs(agent._hook_registry(), custom_reg)
        self.assertIsNot(agent._hook_registry(), get_hook_registry(),
                          "注入的 registry 不应该是全局单例")

    def test_injected_registry_hooks_called_global_not(self):
        """端到端: 注入的 registry 上的 hook 被触发, 全局单例上的 hook 不触发."""
        custom_reg = HookRegistry()
        injected_fn = MagicMock(return_value=None)
        custom_reg.add_pre_tool_call(injected_fn)

        global_fn = MagicMock(return_value=None)
        get_hook_registry().add_pre_tool_call(global_fn)

        deps = build_basic_deps()
        deps.hook_registry = custom_reg

        agent = SimpleAgent(tool_registry=None, deps=deps)
        # 手动调一次 fire — 模拟 react_engine 内部的 fire 路径
        agent._hook_registry().fire(
            "pre_tool_call", "test_tool", {"x": 1}, {"trace_id": "tr-1"},
        )

        injected_fn.assert_called_once()
        global_fn.assert_not_called()  # 全局上的 hook 没被触发


class TestExposedDepsAttribute(unittest.TestCase):
    """SimpleAgent.__init__ 把 deps 存到 self.deps, 让 mixin 走 DI."""

    def setUp(self):
        reset_hook_registry()

    def tearDown(self):
        reset_hook_registry()

    def test_self_deps_is_set(self):
        deps = build_basic_deps()
        agent = SimpleAgent(tool_registry=None, deps=deps)
        self.assertIs(agent.deps, deps)

    def test_deps_has_hook_registry_field(self):
        agent = SimpleAgent(tool_registry=None)
        self.assertIsNotNone(agent.deps)
        # PP (#76) 已经把 hook_registry 灌进 deps
        self.assertIsNotNone(agent.deps.hook_registry)


if __name__ == "__main__":
    unittest.main()
