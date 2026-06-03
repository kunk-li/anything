# -*- coding: utf-8 -*-
"""UP-3: agent always-on 注入用户画像 (方向1)。"""
import unittest

from agent_module.core.impl import SimpleAgent
from state_backend_module import InMemoryBackend
from long_term_memory_module.core.impl import LongTermMemoryImpl
from long_term_memory_module.model import Fact


class _Reg:
    def __init__(self):
        self._t = {}

    def register(self, n, f):
        self._t[n] = f

    def unregister(self, n):
        return self._t.pop(n, None) is not None

    def get(self, n):
        return self._t.get(n)

    def list_tools(self):
        return list(self._t.keys())


class TestUserProfileInjection(unittest.TestCase):
    def _agent_with_profile(self):
        ltm = LongTermMemoryImpl(backend=InMemoryBackend())
        ltm.add_fact(Fact.make(content="偏好简洁直接的回答", content_type="preference", digest="简洁回答"))
        ltm.add_fact(Fact.make(content="易忘先跑测试就提交", content_type="weakness", digest="提醒先测试"))
        return SimpleAgent(tool_registry=_Reg(), llm_planner=None, long_term_memory=ltm)

    def test_inject_prepends_profile_block(self):
        agent = self._agent_with_profile()
        out = agent._inject_user_profile("帮我改个 bug", "default")
        self.assertIn("关于使用者", out)
        self.assertIn("简洁回答", out)
        self.assertIn("需主动补位", out)        # weakness 维度提示
        self.assertIn("提醒先测试", out)
        self.assertIn("帮我改个 bug", out)        # 原任务保留在画像之后

    def test_empty_profile_returns_task_unchanged(self):
        agent = SimpleAgent(tool_registry=_Reg(), llm_planner=None,
                            long_term_memory=LongTermMemoryImpl(backend=InMemoryBackend()))
        self.assertEqual(agent._inject_user_profile("原任务", "default"), "原任务")


if __name__ == "__main__":
    unittest.main()
