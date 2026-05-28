# -*- coding: utf-8 -*-
"""
Skill 系统单元测试 (Task AA #61)
"""

import os
import tempfile
import unittest
from pathlib import Path

from common_utils_module import (
    Skill,
    SkillRegistry,
    inject_skills_into_prompt,
    get_skill_registry,
    reset_skill_registry,
)


SAMPLE_SKILL_MD = """---
name: code_review
description: 审代码
triggers:
  - 代码审查
  - code review
priority: 10
tools:
  - rag_search
---

# 审代码

详细规则...
"""

SAMPLE_NO_FRONTMATTER = "# Plain Body\n\nNo frontmatter here, full file is body."


class TestSkillParseAndMatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        os.environ.pop("ANYTHING_SKILLS_DIR", None)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("ANYTHING_SKILLS_DIR", None)
        reset_skill_registry()

    def test_parse_frontmatter(self):
        from common_utils_module.utils.skills import parse_skill_file
        f = self.tmp_path / "code_review.md"
        f.write_text(SAMPLE_SKILL_MD, encoding="utf-8")
        s = parse_skill_file(f)
        self.assertEqual(s.name, "code_review")
        self.assertEqual(s.description, "审代码")
        self.assertEqual(s.priority, 10)
        self.assertEqual(set(s.triggers), {"代码审查", "code review"})
        self.assertEqual(s.tools, ["rag_search"])
        self.assertIn("详细规则", s.body)

    def test_parse_no_frontmatter(self):
        from common_utils_module.utils.skills import parse_skill_file
        f = self.tmp_path / "plain.md"
        f.write_text(SAMPLE_NO_FRONTMATTER, encoding="utf-8")
        s = parse_skill_file(f)
        self.assertEqual(s.name, "plain")  # 文件名
        self.assertEqual(s.priority, 0)
        self.assertEqual(s.triggers, [])
        self.assertIn("Plain Body", s.body)

    def test_skill_matches(self):
        s = Skill(name="x", triggers=["代码审查", "审一下"])
        self.assertTrue(s.matches("帮我做代码审查"))
        self.assertTrue(s.matches("审一下这段"))
        self.assertFalse(s.matches("天气怎么样"))
        # 大小写不敏感
        s2 = Skill(name="y", triggers=["CODE REVIEW"])
        self.assertTrue(s2.matches("Hey, can you code review this?"))

    def test_skill_matches_empty(self):
        s = Skill(name="x", triggers=[])
        self.assertFalse(s.matches("anything"))
        s2 = Skill(name="y", triggers=["a"])
        self.assertFalse(s2.matches(""))


class TestSkillRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        os.environ.pop("ANYTHING_SKILLS_DIR", None)
        reset_skill_registry()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("ANYTHING_SKILLS_DIR", None)
        reset_skill_registry()

    def test_load_skills_from_dir(self):
        (self.tmp_path / "a.md").write_text(SAMPLE_SKILL_MD, encoding="utf-8")
        (self.tmp_path / "b.md").write_text(SAMPLE_NO_FRONTMATTER, encoding="utf-8")
        reg = SkillRegistry(skills_dir=str(self.tmp_path))
        n = reg.load()
        self.assertEqual(n, 2)
        names = {s.name for s in reg.all_skills()}
        self.assertEqual(names, {"code_review", "b"})

    def test_load_nonexistent_dir(self):
        reg = SkillRegistry(skills_dir=str(self.tmp_path / "nope"))
        self.assertEqual(reg.load(), 0)
        self.assertEqual(reg.all_skills(), [])

    def test_match_ordered_by_priority(self):
        (self.tmp_path / "low.md").write_text(
            "---\nname: low\ntriggers: [common]\npriority: 1\n---\nbody1", encoding="utf-8")
        (self.tmp_path / "high.md").write_text(
            "---\nname: high\ntriggers: [common]\npriority: 9\n---\nbody2", encoding="utf-8")
        reg = SkillRegistry(skills_dir=str(self.tmp_path))
        reg.load()
        matched = reg.match("look for common")
        self.assertEqual([s.name for s in matched], ["high", "low"])

    def test_match_no_query(self):
        reg = SkillRegistry(skills_dir=str(self.tmp_path))
        reg.load()
        self.assertEqual(reg.match(""), [])

    def test_info(self):
        (self.tmp_path / "x.md").write_text(SAMPLE_SKILL_MD, encoding="utf-8")
        reg = SkillRegistry(skills_dir=str(self.tmp_path))
        reg.load()
        info = reg.info()
        self.assertEqual(info["count"], 1)
        self.assertEqual(info["loaded_from"], str(self.tmp_path))
        self.assertEqual(info["skills"][0]["name"], "code_review")
        self.assertGreater(info["skills"][0]["body_chars"], 0)

    def test_env_dir_override(self):
        (self.tmp_path / "envskill.md").write_text(SAMPLE_NO_FRONTMATTER, encoding="utf-8")
        os.environ["ANYTHING_SKILLS_DIR"] = str(self.tmp_path)
        reg = SkillRegistry()
        n = reg.load()
        self.assertEqual(n, 1)


class TestInjectSkills(unittest.TestCase):
    def test_inject_wraps_prompt(self):
        s = Skill(name="code_review", body="审代码: 1) 安全 2) 性能", triggers=["x"])
        out = inject_skills_into_prompt("帮我审下", [s])
        self.assertIn("<Skills>", out)
        self.assertIn("code_review", out)
        self.assertIn("审代码", out)
        self.assertIn("</Skills>", out)
        self.assertIn("<Task>", out)
        self.assertIn("帮我审下", out)

    def test_inject_passthrough_when_empty(self):
        out = inject_skills_into_prompt("hello", [])
        self.assertEqual(out, "hello")

    def test_inject_caps_to_max_skills(self):
        skills = [Skill(name=f"s{i}", body=f"body{i}", triggers=["x"]) for i in range(5)]
        out = inject_skills_into_prompt("p", skills, max_skills=2)
        self.assertIn("s0", out)
        self.assertIn("s1", out)
        self.assertNotIn("s2", out)

    def test_inject_truncates_long_body(self):
        s = Skill(name="long", body="a" * 5000, triggers=["x"])
        out = inject_skills_into_prompt("p", [s], max_body_chars=100)
        self.assertIn("[truncated]", out)


class TestSkillSingleton(unittest.TestCase):
    def test_singleton(self):
        a = get_skill_registry()
        b = get_skill_registry()
        self.assertIs(a, b)
        reset_skill_registry()
        c = get_skill_registry()
        self.assertIsNot(a, c)


if __name__ == "__main__":
    unittest.main()
