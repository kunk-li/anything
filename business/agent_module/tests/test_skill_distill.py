# -*- coding: utf-8 -*-
"""#1 技能自动沉淀: SkillRegistry 可写 + agent._distill_skill 提炼/去重/门控。

用临时 skills 目录 (ANYTHING_SKILLS_DIR) + reset 全局单例隔离, 不碰真 skills/。
复用 test_react 的 _DictRegistry / _ScriptedLLM 构造 agent。
"""
import os
import tempfile
import time
import unittest
from pathlib import Path

from skills_module.impl import SkillRegistry, Skill, get_skill_registry, reset_skill_registry
from agent_module.core.impl import SimpleAgent
from agent_module.tests.test_react import _DictRegistry, _ScriptedLLM


class TestSkillRegistryWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _reg(self):
        r = SkillRegistry(skills_dir=self.tmp)
        r.load()
        return r

    def test_save_writes_file_and_matches_immediately(self):
        reg = self._reg()
        path = reg.save_skill(Skill(
            name="org_files", description="整理文件",
            triggers=["整理文件", "organize files"], tools=["system_info"],
            body="第一步...第二步..."), source="auto")
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.basename(path).startswith("_auto_"))
        # 即时可匹配 (无需重新 load)
        self.assertTrue(any(s.name == "org_files" for s in reg.match("帮我整理文件")))
        # 文件里有 source: auto 标记
        self.assertIn("source: auto", Path(path).read_text(encoding="utf-8"))

    def test_reload_from_disk_keeps_skill(self):
        self._reg().save_skill(Skill(name="s1", triggers=["t1"], body="b1"))
        reg2 = self._reg()  # 全新 registry 从同目录加载
        self.assertTrue(any(s.name == "s1" for s in reg2.all_skills()))

    def test_find_by_triggers_dedup(self):
        reg = self._reg()
        reg.save_skill(Skill(name="a", triggers=["整理文件", "文件归类"], body="x"))
        self.assertIsNotNone(reg.find_by_triggers(["整理文件", "别的"]))   # 有重叠
        self.assertIsNone(reg.find_by_triggers(["完全无关", "xyz"]))        # 无重叠

    def test_save_requires_name_and_triggers(self):
        reg = self._reg()
        self.assertIsNone(reg.save_skill(Skill(name="", triggers=["x"], body="b")))
        self.assertIsNone(reg.save_skill(Skill(name="n", triggers=[], body="b")))


class TestAgentDistill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["ANYTHING_SKILLS_DIR"] = self.tmp
        reset_skill_registry()  # 全局单例从 tmp 重新加载

    def tearDown(self):
        os.environ.pop("ANYTHING_SKILLS_DIR", None)
        reset_skill_registry()

    def _agent(self, llm_out, enable=True, min_tools=2):
        agent = SimpleAgent(tool_registry=_DictRegistry(), llm_planner=_ScriptedLLM([llm_out] * 6))
        agent.enable_skill_distill = enable
        agent.skill_distill_min_tools = min_tools
        return agent

    def test_distill_saves_and_registers(self):
        sj = ('{"name":"check_pc","description":"看本机状态","triggers":["看电脑","电脑使用情况"],'
              '"tools":["system_info"],"body":"调 system_info 读 CPU/内存/磁盘再分析"}')
        agent = self._agent(sj)
        path = agent._distill_skill("看看我电脑用得怎么样",
                                    [{"tool_name": "system_info"}], "你的电脑...", "t")
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(any(s.name == "check_pc" for s in get_skill_registry().match("看电脑状态")))

    def test_distill_dedup_skips_second(self):
        a1 = self._agent('{"name":"check_pc","triggers":["看电脑","电脑使用情况"],"tools":[],"body":"x"}')
        self.assertIsNotNone(a1._distill_skill("看电脑", [{"tool_name": "system_info"}], "a", "t"))
        a2 = self._agent('{"name":"check_pc2","triggers":["看电脑","别的"],"tools":[],"body":"y"}')
        self.assertIsNone(a2._distill_skill("看电脑", [{"tool_name": "system_info"}], "a", "t"))

    def test_distill_bad_json_returns_none(self):
        self.assertIsNone(self._agent("这不是 JSON")._distill_skill(
            "t", [{"tool_name": "x"}], "a", "t"))

    def test_distill_incomplete_json_returns_none(self):
        # 缺 triggers → 不存
        self.assertIsNone(self._agent('{"name":"n","triggers":[],"body":"b"}')._distill_skill(
            "t", [{"tool_name": "x"}], "a", "t"))

    def test_async_gating(self):
        # enable=False → 不沉淀
        self._agent('{"name":"x","triggers":["a"],"body":"b"}', enable=False)._distill_skill_async(
            "t", [{"tool_name": "a"}, {"tool_name": "b"}], "ans", "t")
        # 简单任务 (1 工具 < min 2) → 不沉淀
        self._agent('{"name":"y","triggers":["a"],"body":"b"}', enable=True, min_tools=2)._distill_skill_async(
            "t", [{"tool_name": "a"}], "ans", "t")
        time.sleep(0.4)  # 给可能的线程一点时间 (实际门控在 spawn 前, 不会起线程)
        names = [s.name for s in get_skill_registry().all_skills()]
        self.assertNotIn("x", names)
        self.assertNotIn("y", names)


if __name__ == "__main__":
    unittest.main()
