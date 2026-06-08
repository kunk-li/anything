# -*- coding: utf-8 -*-
"""software_info 工具 (只读查已安装软件版本/用法): 注入 fake backend, 不碰真机/真注册表/不起子进程。

验证: lookup PATH 命令 (版本+用法 / 版本flag回退 / help flag回退 / 截断) /
GUI 注册表回退 / 未找到 / 缺 name / list (注册表 + 过滤 + 限量 + PATH 回退) / 坏 action /
**安全: 含 shell 元字符的软件名绝不进 which/run (不执行)**。
"""
import unittest

from agent_module.tools.tools_impl.software_info import (
    make_software_info_tool, _HELP_CAP,
)


class _FakeBackend:
    """canned which/run/uninstall/path; 记录 which/run 调用以验安全。"""
    def __init__(self, on_path=None, run_map=None, registry=None, path_cmds=None):
        self._on_path = on_path or {}             # name -> resolved path
        self._run_map = run_map or {}             # (argv-tuple) -> (rc, text)
        self._registry = registry or []
        self._path_cmds = path_cmds or []
        self.which_calls = []
        self.run_calls = []

    def which(self, name):
        self.which_calls.append(name)
        return self._on_path.get(name)

    def run(self, argv, timeout=5):
        self.run_calls.append(list(argv))
        return self._run_map.get(tuple(argv), (1, ""))

    def uninstall_entries(self):
        return list(self._registry)

    def path_commands(self, limit=500):
        return list(self._path_cmds)[:limit]


class TestLookupPathCommand(unittest.TestCase):
    def _be(self, **kw):
        return _FakeBackend(**kw)

    def test_version_and_usage(self):
        be = self._be(
            on_path={"git": "/usr/bin/git"},
            run_map={
                ("/usr/bin/git", "--version"): (0, "git version 2.43.0"),
                ("/usr/bin/git", "--help"): (0, "usage: git [opts] <cmd>\n  clone ..."),
            },
        )
        r = make_software_info_tool(be)({"action": "lookup", "name": "git"})
        self.assertEqual(r["code"], "SUCCESS")
        d = r["data"]
        self.assertTrue(d["found"])
        self.assertEqual(d["source"], "path_command")
        self.assertEqual(d["path"], "/usr/bin/git")
        self.assertIn("2.43.0", d["version"])
        self.assertEqual(d["version_flag"], "--version")
        self.assertIn("usage: git", d["usage"])
        self.assertEqual(d["usage_flag"], "--help")

    def test_version_flag_fallback(self):
        # --version 空 → 回退 -V
        be = self._be(
            on_path={"tool": "/bin/tool"},
            run_map={
                ("/bin/tool", "--version"): (1, ""),
                ("/bin/tool", "-V"): (0, "tool 1.0"),
                ("/bin/tool", "--help"): (0, "help text"),
            },
        )
        d = make_software_info_tool(be)({"name": "tool"})["data"]   # action 默认 lookup
        self.assertEqual(d["version_flag"], "-V")
        self.assertIn("1.0", d["version"])

    def test_usage_truncated(self):
        big = "x" * (_HELP_CAP + 500)
        be = self._be(
            on_path={"big": "/bin/big"},
            run_map={
                ("/bin/big", "--version"): (0, "big 9"),
                ("/bin/big", "--help"): (0, big),
            },
        )
        d = make_software_info_tool(be)({"name": "big"})["data"]
        self.assertEqual(len(d["usage"]), _HELP_CAP)
        self.assertTrue(d["usage_truncated"])

    def test_no_version_or_help_still_found(self):
        # 在 PATH 但 version/help 都没输出 → 仍 found=True, version/usage=None
        be = self._be(on_path={"q": "/bin/q"})   # run_map 空 → 全返 (1,"")
        d = make_software_info_tool(be)({"name": "q"})["data"]
        self.assertTrue(d["found"])
        self.assertIsNone(d["version"])
        self.assertIsNone(d["usage"])


class TestLookupRegistryFallback(unittest.TestCase):
    def test_gui_software_via_registry(self):
        # 不在 PATH → 注册表按名子串命中 (GUI 软件)
        be = _FakeBackend(
            registry=[
                {"name": "Google Chrome", "version": "120.0", "publisher": "Google", "location": "C:\\Chrome"},
                {"name": "7-Zip", "version": "23.01", "publisher": None, "location": None},
            ],
        )
        d = make_software_info_tool(be)({"name": "Chrome"})["data"]
        self.assertTrue(d["found"])
        self.assertEqual(d["source"], "registry")
        self.assertEqual(d["matches"][0]["name"], "Google Chrome")
        self.assertEqual(d["matches"][0]["version"], "120.0")

    def test_name_with_space_skips_exec_goes_registry(self):
        # 带空格的名 (如 GUI DisplayName) 不过执行严格校验 → 不 which/不 run, 直接注册表搜
        be = _FakeBackend(registry=[{"name": "Visual Studio Code", "version": "1.85", "location": "C:\\VSCode"}])
        d = make_software_info_tool(be)({"name": "Visual Studio Code"})["data"]
        self.assertTrue(d["found"])
        self.assertEqual(d["source"], "registry")
        self.assertEqual(be.which_calls, [])     # 带空格名绝不进 which
        self.assertEqual(be.run_calls, [])       # 也绝不执行

    def test_not_found(self):
        be = _FakeBackend(registry=[{"name": "Something Else", "version": "1.0"}])
        d = make_software_info_tool(be)({"name": "nope"})["data"]
        self.assertFalse(d["found"])
        self.assertIn("note", d)


class TestLookupSecurity(unittest.TestCase):
    """安全: 含 shell 元字符/路径分隔的软件名绝不进 which/run (不执行任意东西)。"""

    def _assert_no_exec(self, name):
        be = _FakeBackend(on_path={name: "/should/not/matter"})
        r = make_software_info_tool(be)({"name": name})
        self.assertEqual(r["code"], "SUCCESS")     # 优雅返回, 不抛
        self.assertFalse(r["data"]["found"])
        self.assertEqual(be.which_calls, [], f"{name!r} 不该进 which")
        self.assertEqual(be.run_calls, [], f"{name!r} 不该被执行")

    def test_semicolon_injection(self):
        self._assert_no_exec("git;rm")

    def test_pipe_injection(self):
        self._assert_no_exec("a|b")

    def test_path_traversal(self):
        self._assert_no_exec("../evil")

    def test_backtick_and_dollar(self):
        self._assert_no_exec("$(whoami)")

    def test_absolute_path_rejected(self):
        self._assert_no_exec("/bin/sh")


class TestList(unittest.TestCase):
    def test_list_registry_sorted_and_limited(self):
        reg = [{"name": "Zeta", "version": "1"}, {"name": "alpha", "version": "2"},
               {"name": "Mid", "version": "3"}]
        be = _FakeBackend(registry=reg)
        d = make_software_info_tool(be)({"action": "list", "limit": 2})["data"]
        self.assertEqual(d["source"], "registry")
        self.assertEqual(d["total"], 3)
        self.assertEqual(d["returned"], 2)
        self.assertTrue(d["truncated"])
        self.assertEqual([e["name"] for e in d["software"]], ["alpha", "Mid"])  # 大小写不敏感排序

    def test_list_filter(self):
        reg = [{"name": "Python 3.11"}, {"name": "Node.js"}, {"name": "Python 3.12"}]
        be = _FakeBackend(registry=reg)
        d = make_software_info_tool(be)({"action": "list", "filter": "python"})["data"]
        self.assertEqual(d["total"], 2)
        self.assertTrue(all("python" in e["name"].lower() for e in d["software"]))

    def test_list_path_fallback_when_registry_empty(self):
        be = _FakeBackend(registry=[], path_cmds=[{"name": "git", "path": "/usr/bin/git"}])
        d = make_software_info_tool(be)({"action": "list"})["data"]
        self.assertEqual(d["source"], "path_commands")
        self.assertEqual(d["software"][0]["name"], "git")


class TestActionAndArgs(unittest.TestCase):
    def test_bad_action(self):
        r = make_software_info_tool(_FakeBackend())({"action": "delete"})
        self.assertEqual(r["code"], "PARAM_INVALID")

    def test_lookup_missing_name(self):
        r = make_software_info_tool(_FakeBackend())({"action": "lookup"})
        self.assertEqual(r["code"], "PARAM_MISSING")

    def test_default_action_is_lookup(self):
        be = _FakeBackend(on_path={"git": "/g"}, run_map={("/g", "--version"): (0, "git 1")})
        r = make_software_info_tool(be)({"name": "git"})       # 无 action
        self.assertTrue(r["data"]["found"])


if __name__ == "__main__":
    unittest.main()
