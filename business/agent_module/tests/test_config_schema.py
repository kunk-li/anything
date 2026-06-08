# -*- coding: utf-8 -*-
"""配置中枢 schema (执行计划②): catalog / dump / validate 的单测 + 防漂移守护。"""
import unittest

from agent_module.core.impl import SimpleAgent
from agent_module.config.schema import (
    AGENT_CONFIG_SCHEMA, KNOWN_AGENT_KEYS,
    dump_agent_config, validate_agent_config,
)


class _Reg:
    def __init__(self): self._t = {}
    def register(self, n, f): self._t[n] = f
    def unregister(self, n): return self._t.pop(n, None) is not None
    def get(self, n): return self._t.get(n)
    def list_tools(self): return list(self._t.keys())


class _Cfg:
    """最小 config: 只实现 get_config(key, default), 用于 validate 测试。"""
    def __init__(self, agent_section): self._s = agent_section
    def get_config(self, key, default=None):
        return self._s if key == "agent" else default


def _agent():
    return SimpleAgent(tool_registry=_Reg(), llm_planner=None)


class TestSchemaCatalog(unittest.TestCase):
    def test_keys_unique(self):
        keys = [f.key for f in AGENT_CONFIG_SCHEMA]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_attr_field_exists_on_agent(self):
        # 防漂移: schema 里每个声明了 attr 的 flag, SimpleAgent 实例上都得有该属性
        agent = _agent()
        missing = [f.attr for f in AGENT_CONFIG_SCHEMA
                   if f.attr and not hasattr(agent, f.attr)]
        self.assertEqual(missing, [], f"schema 声明但 agent 没有的属性: {missing}")

    def test_type_and_choices_valid(self):
        for f in AGENT_CONFIG_SCHEMA:
            self.assertIn(f.type, ("bool", "int", "str", "list"), f.key)
            if f.choices is not None:
                self.assertEqual(f.type, "str", f"{f.key}: choices 仅用于 str")


class TestDump(unittest.TestCase):
    def test_dump_covers_all_with_current(self):
        agent = _agent()
        dumped = dump_agent_config(agent.config, agent=agent)
        self.assertEqual({d["key"] for d in dumped}, {f.key for f in AGENT_CONFIG_SCHEMA})
        for d in dumped:
            self.assertIn("current", d)
            self.assertIn("group", d)
            self.assertIn("type", d)

    def test_dump_current_reflects_agent(self):
        agent = _agent()
        dumped = {d["key"]: d for d in dump_agent_config(agent.config, agent=agent)}
        self.assertEqual(dumped["agent.execution_strategy"]["current"], agent.execution_strategy)
        # set 型 flag 被 jsonify 成 sorted list
        self.assertEqual(dumped["agent.tool_approval_required"]["current"],
                         sorted(agent.tool_approval_required))

    def test_dump_without_agent_falls_back_to_config(self):
        cfg = _Cfg({})  # 空 agent 段
        dumped = {d["key"]: d for d in dump_agent_config(cfg, agent=None)}
        # 非 agent 属性项 (maintenance_*) 也在, 取默认
        self.assertIn("agent.maintenance_schedule", dumped)
        self.assertEqual(dumped["agent.maintenance_schedule"]["current"], "")


class TestValidate(unittest.TestCase):
    def test_unknown_key_flagged(self):
        unknown = validate_agent_config(_Cfg({"execution_strategy": "react", "bogus_flag": 1}))
        self.assertEqual(unknown, ["bogus_flag"])

    def test_all_known_returns_empty(self):
        unknown = validate_agent_config(_Cfg({"timeout": 30, "verify_mode": "auto"}))
        self.assertEqual(unknown, [])

    def test_missing_section_safe(self):
        self.assertEqual(validate_agent_config(_Cfg(None)), [])

    def test_real_config_has_no_undeclared_keys(self):
        # 守护: config.yaml 的 agent.* 顶层 key 必须全在 schema 里 (新增 config 须同步 schema)
        agent = _agent()
        self.assertEqual(validate_agent_config(agent.config), [])


if __name__ == "__main__":
    unittest.main()
