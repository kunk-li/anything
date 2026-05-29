# -*- coding: utf-8 -*-
"""
install_audit_hooks(registry=...) DI 验证 (Task CCC #89).

证明加进去的 registry 参数真的能让 hook 装到指定的 HookRegistry,
而不是全局单例.
"""
import os
import tempfile
import unittest

from audit_module import install_audit_hooks, get_audit_logger, reset_audit_logger
from hooks_module import HookRegistry, get_hook_registry, reset_hook_registry


class TestInstallAuditHooksDI(unittest.TestCase):

    def setUp(self):
        reset_hook_registry()
        reset_audit_logger()
        # 临时 audit log 文件
        fd, self._audit_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)

    def tearDown(self):
        reset_hook_registry()
        reset_audit_logger()
        try:
            os.unlink(self._audit_path)
        except Exception:
            pass

    def test_default_registry_uses_global(self):
        """没传 registry → 默认装到全局单例 (back-compat)."""
        before_counts = get_hook_registry().count()
        install_audit_hooks(path=self._audit_path)
        after_counts = get_hook_registry().count()
        # 应当至少装了 pre_tool_call / post_tool_call / pre_llm_call / post_llm_call 几个
        total_before = sum(before_counts.values())
        total_after = sum(after_counts.values())
        self.assertGreater(total_after, total_before, "hook 应该装到全局 registry")

    def test_injected_registry_receives_hooks_not_global(self):
        """注入自定义 registry → hook 装到它, 全局单例不动."""
        custom_reg = HookRegistry()
        before_global = sum(get_hook_registry().count().values())
        before_custom = sum(custom_reg.count().values())

        install_audit_hooks(path=self._audit_path, registry=custom_reg)

        after_global = sum(get_hook_registry().count().values())
        after_custom = sum(custom_reg.count().values())

        # 全局完全不变
        self.assertEqual(after_global, before_global)
        # 自定义 registry 收到了 hooks
        self.assertGreater(after_custom, before_custom)


class TestInstallQuotaHooksDI(unittest.TestCase):

    def setUp(self):
        reset_hook_registry()
        # 也得 reset quota guard, 避免相互污染
        from quota_module import reset_quota_guard
        reset_quota_guard()

    def tearDown(self):
        reset_hook_registry()
        from quota_module import reset_quota_guard
        reset_quota_guard()

    def test_default_registry_uses_global(self):
        from quota_module import install_quota_hooks
        before = sum(get_hook_registry().count().values())
        install_quota_hooks(rate_per_minute=10)
        after = sum(get_hook_registry().count().values())
        self.assertGreater(after, before)

    def test_injected_registry_receives_hooks_not_global(self):
        from quota_module import install_quota_hooks
        custom_reg = HookRegistry()
        before_global = sum(get_hook_registry().count().values())
        before_custom = sum(custom_reg.count().values())

        install_quota_hooks(rate_per_minute=10, registry=custom_reg)

        after_global = sum(get_hook_registry().count().values())
        after_custom = sum(custom_reg.count().values())

        self.assertEqual(after_global, before_global)
        self.assertGreater(after_custom, before_custom)


if __name__ == "__main__":
    unittest.main()
