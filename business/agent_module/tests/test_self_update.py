# -*- coding: utf-8 -*-
"""影子模式自更新 (执行计划⑧): 全绿测试当安全闸 + 隔离 worktree + 绝不自动应用 + fail-safe。

注入 fake worktree / test_runner, 不动真 git、不跑真 pytest。
"""
import os
import subprocess
import unittest
from unittest import mock

from agent_module.core.impl import SimpleAgent
from agent_module.core.self_update import ShadowSelfUpdate, _GitWorktree


class _FakeWorktree:
    def __init__(self, apply_ok=True, create_raises=False):
        self.apply_ok = apply_ok
        self.create_raises = create_raises
        self.created = []
        self.cleaned = []

    def create(self):
        if self.create_raises:
            raise RuntimeError("worktree create boom")
        p = f"/tmp/fakewt{len(self.created)}"
        self.created.append(p)
        return p

    def apply_diff(self, path, diff):
        return self.apply_ok

    def cleanup(self, path):
        self.cleaned.append(path)


class _Reg:
    def __init__(self): self._t = {}
    def register(self, n, f): self._t[n] = f
    def unregister(self, n): return self._t.pop(n, None) is not None
    def get(self, n): return self._t.get(n)
    def list_tools(self): return list(self._t.keys())


class TestRunTestGate(unittest.TestCase):
    def test_pass(self):
        su = ShadowSelfUpdate(repo_root="/x", test_runner=lambda cwd: (0, "1225 passed"))
        g = su.run_test_gate()
        self.assertTrue(g["passed"])
        self.assertEqual(g["returncode"], 0)

    def test_fail(self):
        su = ShadowSelfUpdate(repo_root="/x", test_runner=lambda cwd: (1, "3 failed"))
        self.assertFalse(su.run_test_gate()["passed"])

    def test_runner_exception_is_fail(self):
        def boom(cwd):
            raise RuntimeError("runner down")
        su = ShadowSelfUpdate(repo_root="/x", test_runner=boom)
        self.assertFalse(su.run_test_gate()["passed"])


class TestVerifyInShadow(unittest.TestCase):
    def _su(self, wt, rc=0):
        return ShadowSelfUpdate(repo_root="/x", test_runner=lambda cwd: (rc, f"rc={rc}"), worktree=wt)

    def test_gate_pass_cleans_up(self):
        wt = _FakeWorktree()
        r = self._su(wt, rc=0).verify_in_shadow({"id": "p1", "diff": "--- a\n+++ b\n"})
        self.assertTrue(r["gate_passed"])
        self.assertTrue(r["applied"])
        self.assertEqual(wt.cleaned, wt.created)        # worktree 必清理

    def test_gate_fail_blocks(self):
        wt = _FakeWorktree()
        r = self._su(wt, rc=1).verify_in_shadow({"id": "p2", "diff": "d"})
        self.assertFalse(r["gate_passed"])              # 测试不过 → 安全闸拦截
        self.assertTrue(wt.cleaned)                     # 仍清理

    def test_diff_apply_fail_skips_gate(self):
        wt = _FakeWorktree(apply_ok=False)
        r = self._su(wt, rc=0).verify_in_shadow({"diff": "bad-diff"})
        self.assertFalse(r["applied"])
        self.assertFalse(r["gate_passed"])              # 没应用上 → 不进闸 → 拒绝
        self.assertTrue(wt.cleaned)

    def test_no_diff_runs_baseline(self):
        wt = _FakeWorktree()
        r = self._su(wt, rc=0).verify_in_shadow({"id": "p3"})   # 无 diff = 干净副本验基线
        self.assertTrue(r["applied"])
        self.assertTrue(r["gate_passed"])

    def test_create_exception_failsafe(self):
        wt = _FakeWorktree(create_raises=True)
        r = self._su(wt, rc=0).verify_in_shadow({"diff": "d"})
        self.assertFalse(r["gate_passed"])              # 异常 → fail-safe 拒绝
        self.assertEqual(wt.cleaned, [])                # 没建成 → 无需清理, 不崩


class TestGitWorktreeCreate(unittest.TestCase):
    def test_create_failure_removes_temp_dir(self):
        # git worktree add 失败时, mkdtemp 落盘的临时目录必须被清掉 (否则泄漏)
        wt = _GitWorktree(repo_root="/x")
        captured = {}

        def fake_run(cmd, *a, **kw):
            # cmd: ["git", "-C", repo_root, "worktree", "add", "--detach", <tmpdir>, "HEAD"]
            captured["dir"] = cmd[6]
            raise subprocess.CalledProcessError(128, cmd, stderr="fatal: boom")

        with mock.patch("agent_module.core.self_update.subprocess.run", side_effect=fake_run):
            with self.assertRaises(subprocess.CalledProcessError):
                wt.create()

        self.assertIn("dir", captured)                      # 确认 mkdtemp 真建了目录并传给了 git
        self.assertFalse(os.path.exists(captured["dir"]))   # 失败后该临时目录已被删除, 无泄漏


class TestAgentHook(unittest.TestCase):
    def test_default_off(self):
        agent = SimpleAgent(tool_registry=_Reg(), llm_planner=None)
        r = agent.verify_self_update({"diff": "d"})
        self.assertIs(r["enabled"], False)              # 默认关 — 安全
        self.assertFalse(r["gate_passed"])

    def test_method_exists_and_gated(self):
        agent = SimpleAgent(tool_registry=_Reg(), llm_planner=None)
        self.assertTrue(hasattr(agent, "verify_self_update"))
        self.assertFalse(agent.enable_self_update)       # 默认 False


if __name__ == "__main__":
    unittest.main()
