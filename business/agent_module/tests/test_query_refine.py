# -*- coding: utf-8 -*-
"""UP-4 (方向1 阶段2): query refinement — 用户问得含糊时基于画像主动优化提问。

验证 _refine_query 的核心逻辑与各 fail-open 分支:
    1. 含糊问题 + 有画像 + LLM 判 needs_refine → 改写 + meta
    2. 清楚问题 (LLM 判 needs_refine=false) → 原样, meta=None
    3. 无画像 → 原样, 且不调 LLM (gate)
    4. 无 LLM 通道 → 原样
    5. 长问题 → gate 拦截, 不调 LLM
    6. 改写过短 → 安全阀拦截, 用原问题
    7. LLM 返回非 JSON / LLM 抛异常 → fail-open 用原问题
    8. 配置默认关 + max_len 默认 200
"""
import unittest

from agent_module.core.impl import SimpleAgent
from state_backend_module import InMemoryBackend
from long_term_memory_module.core.impl import LongTermMemoryImpl
from long_term_memory_module.model import Fact


class _Reg:
    """最小 tool registry, 不注册 llm_generate → 无显式 llm_planner 时无 LLM 通道。"""
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


class _RecordingLLM:
    """callable LLM mock: 记录调用次数, 返回固定响应 (模拟判含糊+改写)。"""
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


def _ltm_with_profile():
    ltm = LongTermMemoryImpl(backend=InMemoryBackend())
    ltm.add_fact(Fact.make(content="日常用 Python 开发", content_type="domain", digest="Python 开发者"))
    ltm.add_fact(Fact.make(content="偏好带类型标注和单测", content_type="preference", digest="要类型标注+测试"))
    return ltm


class TestRefineQuery(unittest.TestCase):
    def _agent(self, llm, ltm=None):
        return SimpleAgent(
            tool_registry=_Reg(), llm_planner=llm,
            long_term_memory=ltm if ltm is not None else _ltm_with_profile(),
        )

    def test_ambiguous_query_refined_with_profile(self):
        llm = _RecordingLLM(
            '{"needs_refine": true, '
            '"refined": "用 Python 写一个带类型标注的快速排序, 并附 pytest 测试", '
            '"reason": "补全了语言/类型标注/测试偏好"}'
        )
        agent = self._agent(llm)
        refined, meta = agent._refine_query("写个排序", "default", "tr1")
        self.assertIn("Python", refined)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["original"], "写个排序")
        self.assertIn("Python", meta["refined"])
        self.assertTrue(meta["reason"])
        self.assertEqual(len(llm.calls), 1)

    def test_clear_query_not_refined(self):
        llm = _RecordingLLM('{"needs_refine": false, "refined": "", "reason": ""}')
        agent = self._agent(llm)
        task = "用 Python 写快排并加 pytest 测试"
        refined, meta = agent._refine_query(task, "default", "tr1")
        self.assertEqual(refined, task)
        self.assertIsNone(meta)

    def test_no_profile_returns_unchanged_and_skips_llm(self):
        llm = _RecordingLLM('{"needs_refine": true, "refined": "x x x x x", "reason": "y"}')
        agent = self._agent(llm, ltm=LongTermMemoryImpl(backend=InMemoryBackend()))
        refined, meta = agent._refine_query("写个排序", "default", "tr1")
        self.assertEqual(refined, "写个排序")
        self.assertIsNone(meta)
        self.assertEqual(len(llm.calls), 0)  # 无画像 → gate 提前返回, 不调 LLM

    def test_no_llm_returns_unchanged(self):
        # llm_planner=None 且 registry 无 llm_generate → 无 LLM 通道
        agent = SimpleAgent(
            tool_registry=_Reg(), llm_planner=None,
            long_term_memory=_ltm_with_profile(),
        )
        refined, meta = agent._refine_query("写个排序", "default", "tr1")
        self.assertEqual(refined, "写个排序")
        self.assertIsNone(meta)

    def test_long_query_gated_before_llm(self):
        llm = _RecordingLLM('{"needs_refine": true, "refined": "x x x x x", "reason": "y"}')
        agent = self._agent(llm)
        agent.query_refine_max_len = 20
        long_task = "这是一个已经写得非常详细非常具体的长问题" * 3
        refined, meta = agent._refine_query(long_task, "default", "tr1")
        self.assertEqual(refined, long_task)
        self.assertIsNone(meta)
        self.assertEqual(len(llm.calls), 0)  # 超长 gate → 不调 LLM

    def test_short_refined_rejected_by_safety_valve(self):
        # needs_refine=true 但 refined 过短 (< len(task)//4) → 安全阀拦截
        llm = _RecordingLLM('{"needs_refine": true, "refined": "x", "reason": "被截断"}')
        agent = self._agent(llm)
        task = "帮我把这段很长的话理解清楚然后给出处理方案谢谢"
        refined, meta = agent._refine_query(task, "default", "tr1")
        self.assertEqual(refined, task)
        self.assertIsNone(meta)

    def test_malformed_json_fail_open(self):
        llm = _RecordingLLM("这不是 JSON, LLM 抽风了")
        agent = self._agent(llm)
        refined, meta = agent._refine_query("写个排序", "default", "tr1")
        self.assertEqual(refined, "写个排序")
        self.assertIsNone(meta)

    def test_llm_exception_fail_open(self):
        def _boom(prompt):
            raise RuntimeError("LLM down")
        agent = self._agent(_boom)
        refined, meta = agent._refine_query("写个排序", "default", "tr1")
        self.assertEqual(refined, "写个排序")
        self.assertIsNone(meta)


class TestRefineConfig(unittest.TestCase):
    def test_defaults(self):
        agent = SimpleAgent(
            tool_registry=_Reg(), llm_planner=None,
            long_term_memory=_ltm_with_profile(),
        )
        self.assertFalse(agent.enable_query_refine)   # 默认关
        self.assertEqual(agent.query_refine_max_len, 200)


if __name__ == "__main__":
    unittest.main()
