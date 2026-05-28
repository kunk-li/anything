# -*- coding: utf-8 -*-
"""
ProjectMemory 单元测试 (Task U #55)

覆盖:
    - 文件不存在 -> load() 返回空字符串
    - 显式路径优先于 env / 默认候选
    - 环境变量 ANYTHING_PROJECT_MEMORY 覆盖默认候选
    - mtime 变化 -> 自动重读
    - max_chars 截断生效
    - inject_into_prompt 拼接
    - get_project_memory 单例 / reset 行为
"""

import os
import tempfile
import time
import unittest
from pathlib import Path

from common_utils_module import (
    ProjectMemory,
    get_project_memory,
    reset_project_memory,
)


class TestProjectMemory(unittest.TestCase):
    def setUp(self):
        # 每个测试用独立 tempdir 隔离
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        # 清环境变量, 避免互相干扰
        os.environ.pop("ANYTHING_PROJECT_MEMORY", None)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("ANYTHING_PROJECT_MEMORY", None)
        reset_project_memory()

    def test_no_file_returns_empty(self):
        # 显式指向一个不存在的路径
        mem = ProjectMemory(explicit_path=str(self.tmp_path / "missing.md"))
        self.assertEqual(mem.load(), "")
        self.assertIsNone(mem.loaded_path)

    def test_explicit_path_loaded(self):
        p = self.tmp_path / "MY_MEMORY.md"
        p.write_text("hello memory", encoding="utf-8")
        mem = ProjectMemory(explicit_path=str(p))
        content = mem.load()
        self.assertEqual(content, "hello memory")
        self.assertEqual(mem.loaded_path, str(p))

    def test_env_var_overrides_candidates(self):
        # 默认候选不会去找环境变量指向的文件, 但 env 优先
        target = self.tmp_path / "env_memory.md"
        target.write_text("env-loaded", encoding="utf-8")
        os.environ["ANYTHING_PROJECT_MEMORY"] = str(target)

        # candidates 设成不存在的, env 应该优先
        mem = ProjectMemory(candidates=["__nonexistent__.md"])
        self.assertEqual(mem.load(), "env-loaded")

    def test_default_candidates_in_cwd(self):
        original_cwd = Path.cwd()
        try:
            os.chdir(self.tmp_path)
            agents_md = self.tmp_path / "AGENTS.md"
            agents_md.write_text("anything-rules", encoding="utf-8")
            mem = ProjectMemory()
            self.assertEqual(mem.load(), "anything-rules")
        finally:
            os.chdir(original_cwd)

    def test_mtime_hot_reload(self):
        p = self.tmp_path / "AGENTS.md"
        p.write_text("v1", encoding="utf-8")
        mem = ProjectMemory(explicit_path=str(p))
        self.assertEqual(mem.load(), "v1")

        # 修改文件 (确保 mtime 变化)
        time.sleep(1.1)
        p.write_text("v2", encoding="utf-8")
        os.utime(p, None)
        self.assertEqual(mem.load(), "v2")

    def test_max_chars_truncate(self):
        p = self.tmp_path / "huge.md"
        p.write_text("a" * 200, encoding="utf-8")
        mem = ProjectMemory(explicit_path=str(p), max_chars=50)
        out = mem.load()
        self.assertTrue(out.startswith("a" * 50))
        self.assertIn("[truncated]", out)

    def test_inject_into_prompt_wraps(self):
        p = self.tmp_path / "AGENTS.md"
        p.write_text("the project follows rule X", encoding="utf-8")
        mem = ProjectMemory(explicit_path=str(p))
        out = mem.inject_into_prompt("what is the project rule?")
        self.assertIn("<ProjectMemory>", out)
        self.assertIn("the project follows rule X", out)
        self.assertIn("</ProjectMemory>", out)
        self.assertIn("<Task>", out)
        self.assertIn("what is the project rule?", out)

    def test_inject_passthrough_when_no_memory(self):
        # 文件不存在 -> 原 prompt 不动
        mem = ProjectMemory(explicit_path=str(self.tmp_path / "missing.md"))
        out = mem.inject_into_prompt("hello")
        self.assertEqual(out, "hello")

    def test_get_project_memory_singleton(self):
        a = get_project_memory()
        b = get_project_memory()
        self.assertIs(a, b)

    def test_reset_project_memory(self):
        a = get_project_memory()
        reset_project_memory()
        b = get_project_memory()
        self.assertIsNot(a, b)

    def test_info_returns_path_and_length(self):
        p = self.tmp_path / "AGENTS.md"
        p.write_text("12345", encoding="utf-8")
        mem = ProjectMemory(explicit_path=str(p))
        path, n = mem.info()
        self.assertEqual(path, str(p))
        self.assertEqual(n, 5)


if __name__ == "__main__":
    unittest.main()
