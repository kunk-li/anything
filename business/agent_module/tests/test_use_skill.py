# -*- coding: utf-8 -*-
"""集成 superpowers: SkillRegistry 递归加载/按名取/目录 + use_skill 工具。

用临时目录隔离, 不碰真 skills/。"""
import tempfile
import unittest
from pathlib import Path

from skills_module.impl import SkillRegistry
from agent_module.tools.tools_impl.use_skill import make_use_skill_tool


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class TestSkillRegistrySuperpowers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # superpowers 式嵌套布局: skills/testing/tdd/SKILL.md
        _write(Path(self.tmp) / "testing" / "tdd" / "SKILL.md",
               "---\nname: tdd\ndescription: 测试驱动开发 RED-GREEN-REFACTOR\ntriggers:\n  - tdd\n---\n\n先写失败测试, 再实现, 再重构。")
        _write(Path(self.tmp) / "flat.md",
               "---\nname: flat_skill\ndescription: 平铺技能\n---\n\nbody here")
        _write(Path(self.tmp) / "README.md", "# 这是文档, 无 frontmatter")  # 裸文档应被 catalog 跳过
        self.reg = SkillRegistry(skills_dir=self.tmp)
        self.reg.load()

    def test_recursive_load_subdir_and_flat(self):
        names = [s.name for s in self.reg.all_skills()]
        self.assertIn("tdd", names)         # 子目录里的
        self.assertIn("flat_skill", names)  # 平铺的

    def test_get_by_name_case_insensitive(self):
        self.assertEqual(self.reg.get_by_name("tdd").name, "tdd")
        self.assertEqual(self.reg.get_by_name("TDD").name, "tdd")
        self.assertIsNone(self.reg.get_by_name("nope"))

    def test_catalog_skips_bare_docs(self):
        names = [c["name"] for c in self.reg.catalog()]
        self.assertIn("tdd", names)
        self.assertIn("flat_skill", names)
        self.assertNotIn("README", names)  # 无描述无 trigger 的裸文档不进目录


class TestUseSkillTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _write(Path(self.tmp) / "s.md",
               "---\nname: debug\ndescription: 系统化调试\ntriggers:\n  - 调试\ntools:\n  - shell_exec\n---\n\n第一步复现, 第二步定位根因...")
        self.reg = SkillRegistry(skills_dir=self.tmp)
        self.reg.load()
        self.tool = make_use_skill_tool(registry_getter=lambda: self.reg)

    def test_loads_full_body(self):
        r = self.tool({"name": "debug"})
        self.assertEqual(r["code"], "SUCCESS")
        self.assertIn("复现", r["data"]["body"])
        self.assertEqual(r["data"]["tools"], ["shell_exec"])

    def test_not_found_lists_available(self):
        r = self.tool({"name": "不存在的技能"})
        self.assertEqual(r["code"], "SKILL_NOT_FOUND")
        self.assertIn("debug", r["message"])

    def test_missing_name(self):
        self.assertEqual(self.tool({})["code"], "PARAM_MISSING")


if __name__ == "__main__":
    unittest.main()
