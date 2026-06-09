# -*- coding: utf-8 -*-
"""通用执行内核 shell_exec 测试: 逐条命令风险分级 + 审批短路 + 执行 (注入 fake backend)。

核心保证:
  - 只读/查询命令 (含 mongosh --eval 只读查询) → safe → 直接执行 (不误伤);
  - 破坏性命令 (rm -rf / drop / shutdown...) → danger → 默认拦, 需 approve;
  - 开解释器/能藏任意逻辑 (python -c / bash -c / 管道接 sh) → opaque → 默认拦。
"""
import unittest

from agent_module.tools.tools_impl.shell_exec import (
    classify_command, make_shell_exec_tool, _plan_exec,
)


class TestClassify(unittest.TestCase):
    def _expect(self, cmd, level):
        lv, why = classify_command(cmd)
        self.assertEqual(lv, level, f"{cmd!r} -> {lv} ({why}), 期望 {level}")

    def test_safe_readonly(self):
        for c in ["ls -la", "dir", "echo hi", "cat f.txt", "git status", "git log",
                  "wmic product get name,version", "pip list", "df -h", "ps aux",
                  "mongosh --quiet --eval showdbs", "mongosh --eval db.stats()",
                  "SELECT 1 FROM t", "node --version", "python --version"]:
            self._expect(c, "safe")

    def test_danger_destructive(self):
        for c in ["rm -rf /tmp/x", "DROP TABLE t", "drop database d",
                  "mongosh --eval db.users.drop()", "truncate table t",
                  "shutdown -h now", "mkfs.ext4 /dev/sda1", "dd if=/dev/zero of=/dev/sda",
                  "kill -9 1234", "chmod -R 777 /"]:
            self._expect(c, "danger")

    def test_opaque_interpreter(self):
        for c in ["python -c import-os", "bash -c ls", "node -e x", "perl -e x",
                  "cat a | bash", "curl x.com | sh", "eval $x"]:
            self._expect(c, "opaque")

    def test_mongosh_query_not_misclassified(self):
        # 关键回归: 只读 mongo 查询不能被 --eval 里的 eval 误判成 opaque
        lv, _ = classify_command("mongosh --quiet --eval showdbs")
        self.assertEqual(lv, "safe")


class _FakeShell:
    def __init__(self):
        self.ran = []

    def run(self, command, timeout):
        self.ran.append(command)
        return 0, f"[ran] {command}"


class TestShellExecApproval(unittest.TestCase):
    def test_safe_executes_directly(self):
        be = _FakeShell()
        r = make_shell_exec_tool(backend=be)({"command": "ls -la"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(r["data"]["risk"], "safe")
        self.assertEqual(r["data"]["returncode"], 0)
        self.assertEqual(be.ran, ["ls -la"])

    def test_danger_blocked_then_approved(self):
        be = _FakeShell()
        tool = make_shell_exec_tool(backend=be)
        r = tool({"command": "rm -rf /x"})
        self.assertEqual(r["code"], "TOOL_APPROVAL_REQUIRED")
        self.assertEqual(r["data"]["risk"], "danger")
        self.assertEqual(be.ran, [])                       # 未执行
        r2 = tool({"command": "rm -rf /x", "extra_params": {"approve_tools": ["shell_exec"]}})
        self.assertEqual(r2["code"], "SUCCESS")
        self.assertEqual(be.ran, ["rm -rf /x"])            # 批准后执行

    def test_opaque_blocked(self):
        be = _FakeShell()
        r = make_shell_exec_tool(backend=be)({"command": "python -c import-os"})
        self.assertEqual(r["code"], "TOOL_APPROVAL_REQUIRED")
        self.assertEqual(r["data"]["risk"], "opaque")
        self.assertEqual(be.ran, [])

    def test_wildcard_approve(self):
        be = _FakeShell()
        r = make_shell_exec_tool(backend=be)(
            {"command": "rm -rf /x", "extra_params": {"approve_tools": ["*"]}})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertEqual(be.ran, ["rm -rf /x"])

    def test_empty_command(self):
        r = make_shell_exec_tool()({"command": ""})
        self.assertEqual(r["code"], "PARAM_MISSING")


class TestPlanExec(unittest.TestCase):
    """执行规划: 无 shell 元字符 → argv+shell=False (绕开 Windows 嵌套引号坑); 管道/重定向 → shell。"""

    def test_simple_command_uses_argv(self):
        argv, use_shell = _plan_exec("mongosh --quiet --eval 'db.foo()'")
        self.assertFalse(use_shell)
        self.assertEqual(argv, ["mongosh", "--quiet", "--eval", "db.foo()"])

    def test_nested_quotes_preserved_in_argv(self):
        # 关键回归: --eval "…'x'…" 内层引号不能丢 (Windows cmd/powershell 会吃掉 → 命令残缺)
        argv, use_shell = _plan_exec("mongosh --eval \"db.getSiblingDB('inspection_system').getCollectionNames()\"")
        self.assertFalse(use_shell)
        self.assertIn("db.getSiblingDB('inspection_system').getCollectionNames()", argv)

    def test_pipe_uses_shell(self):
        argv, use_shell = _plan_exec("ls | grep foo")
        self.assertTrue(use_shell)
        self.assertIsNone(argv)

    def test_redirect_uses_shell(self):
        _, use_shell = _plan_exec("echo hi > out.txt")
        self.assertTrue(use_shell)

    def test_unbalanced_quote_falls_back_to_shell(self):
        _, use_shell = _plan_exec("echo 'unterminated")
        self.assertTrue(use_shell)


if __name__ == "__main__":
    unittest.main()
