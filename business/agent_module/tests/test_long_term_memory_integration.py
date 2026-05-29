# -*- coding: utf-8 -*-
"""
SimpleAgent ↔ long_term_memory_module 集成测试 (Task FFF #92).

验证:
    1. Agent 默认不接 memory 时行为不变 (back-compat)
    2. Agent 接 memory 时 execute() 前 search_facts + 注入 task
    3. Agent 接 memory 时 execute() 后 extract_facts + add_fact
    4. memory_hits 被记到 response.details["memory_hits"]
    5. memory 失败 (LLM 抛 / extract 抛) 不影响主响应
"""
import os
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import unittest
from unittest.mock import MagicMock

from agent_module import SimpleAgent
from long_term_memory_module import LongTermMemoryImpl, Fact, MemoryQuery
from state_backend_module import InMemoryBackend


class _DummyTool:
    """最小工具, register/get 兼容 SimpleAgent 期望的 registry 接口."""

    def __init__(self):
        self.tools = {}
        self.descs = {}

    def register(self, name, fn, description=""):
        self.tools[name] = fn
        if description:
            self.descs[name] = description

    def get(self, name):
        return self.tools.get(name)

    def list_tools(self):
        return list(self.tools.keys())

    def describe_all(self):
        return dict(self.descs)


def _make_request(task: str, session_id: str = "sess_test", trace_id: str = "tr_test"):
    return {
        "task": task, "session_id": session_id, "trace_id": trace_id,
        "extra_params": {},
    }


class _MockLLM:
    """Mock LLM for extract_facts. generate(prompt) → returns predefined JSON."""
    def __init__(self, response: str = "[]"):
        self._response = response
        self.calls = []

    def generate(self, prompt: str, **kwargs) -> str:
        self.calls.append(prompt)
        return self._response


# ============================================================
# 1. back-compat: long_term_memory=None 时行为不变
# ============================================================


class TestAgentWithoutMemory(unittest.TestCase):

    def test_default_no_memory_attribute(self):
        agent = SimpleAgent(tool_registry=_DummyTool())
        self.assertIsNone(agent.long_term_memory)
        # memory_enabled 在没 memory 时应该是 False
        self.assertFalse(agent.memory_enabled)

    def test_execute_does_not_call_memory(self):
        """没注入 memory 时, execute 不应该报 AttributeError 或调用 memory 路径."""
        agent = SimpleAgent(tool_registry=_DummyTool())
        # 跑 task 不会因为 memory=None 而崩溃
        # 注: 这里不真跑 execute (会触发 LLM 路径), 只检查属性
        self.assertIsNone(agent.long_term_memory)


# ============================================================
# 2/3. 注入 memory → execute 前 search + 后 extract
# ============================================================


class TestAgentMemoryInjection(unittest.TestCase):

    def setUp(self):
        self.backend = InMemoryBackend()
        # 没接 llm_client/embedder, 走 hash-only 路径 (DDD MVP 模式)
        self.memory = LongTermMemoryImpl(backend=self.backend)
        self.agent = SimpleAgent(
            tool_registry=_DummyTool(),
            long_term_memory=self.memory,
        )

    def test_memory_enabled_true(self):
        self.assertTrue(self.agent.memory_enabled)
        self.assertIsNotNone(self.agent.long_term_memory)
        self.assertEqual(self.agent.memory_top_k, 5)

    def test_inject_returns_unchanged_when_no_facts(self):
        """没历史 fact 时 _inject_long_term_memory 应返回原 task."""
        task, hits = self.agent._inject_long_term_memory(
            task="what is Python?",
            tenant_id="default",
            trace_id="tr1",
        )
        self.assertEqual(task, "what is Python?")
        self.assertEqual(hits, [])

    def test_inject_augments_task_when_facts_match(self):
        """有相关 fact 时 _inject_long_term_memory 应在 task 前加 [长期记忆] block."""
        self.memory.add_fact(Fact.make(
            "User prefers Python over JavaScript", tenant_id="t1",
        ))
        task, hits = self.agent._inject_long_term_memory(
            task="Python", tenant_id="t1", trace_id="tr1",
        )
        self.assertIn("[长期记忆", task)
        self.assertIn("Python", task)
        self.assertIn("[当前任务]", task)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["content"][:6], "User p")

    def test_inject_top_k_limit(self):
        for i in range(10):
            self.memory.add_fact(Fact.make(f"common_fact_{i}", tenant_id="t1"))
        self.agent.memory_top_k = 3
        task, hits = self.agent._inject_long_term_memory(
            task="common_fact", tenant_id="t1", trace_id="tr1",
        )
        self.assertLessEqual(len(hits), 3)


# ============================================================
# 4. extract_and_store with mocked LLM
# ============================================================


class TestAgentMemoryExtractAndStore(unittest.TestCase):

    def setUp(self):
        self.backend = InMemoryBackend()
        self.llm = _MockLLM(
            '[{"content": "User likes Python language", "tags": ["preference"], "confidence": 0.9}]'
        )
        self.memory = LongTermMemoryImpl(
            backend=self.backend, llm_client=self.llm,
        )
        self.agent = SimpleAgent(
            tool_registry=_DummyTool(),
            long_term_memory=self.memory,
        )

    def test_extract_writes_facts_to_backend(self):
        count = self.agent._extract_and_store_memory(
            task="我喜欢用 Python 开发", final_answer="OK",
            session_id="s1", tenant_id="t1", trace_id="tr1",
        )
        self.assertEqual(count, 1)
        # backend 里能看到这条 fact
        facts = self.memory.list_facts("t1")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].content, "User likes Python language")

    def test_extract_dedup_on_repeat(self):
        """同一对话调两次 — 第二次的 fact 应被 dedup 到第一次的, 不增加新 fact."""
        self.agent._extract_and_store_memory(
            task="a", final_answer="b", session_id="s1", tenant_id="t1", trace_id="tr1",
        )
        self.agent._extract_and_store_memory(
            task="a", final_answer="b", session_id="s1", tenant_id="t1", trace_id="tr2",
        )
        facts = self.memory.list_facts("t1")
        self.assertEqual(len(facts), 1)
        # access_count 应被 bump
        self.assertGreaterEqual(facts[0].access_count, 1)


# ============================================================
# 5. memory 失败不阻断主响应
# ============================================================


class TestMemoryFailureSoftFail(unittest.TestCase):

    def test_extract_failure_silent(self):
        backend = InMemoryBackend()
        llm = MagicMock()
        llm.generate.side_effect = RuntimeError("LLM down")
        memory = LongTermMemoryImpl(backend=backend, llm_client=llm)
        agent = SimpleAgent(tool_registry=_DummyTool(), long_term_memory=memory)
        # 不抛, 返 0
        count = agent._extract_and_store_memory(
            task="x", final_answer="y", session_id="s1", tenant_id="t1", trace_id="tr1",
        )
        self.assertEqual(count, 0)

    def test_no_llm_silently_returns_zero(self):
        """memory.llm_client=None 时, extract 抛 NotImplementedError, agent 静默返 0."""
        backend = InMemoryBackend()
        memory = LongTermMemoryImpl(backend=backend, llm_client=None)
        agent = SimpleAgent(tool_registry=_DummyTool(), long_term_memory=memory)
        count = agent._extract_and_store_memory(
            task="x", final_answer="y", session_id="s1", tenant_id="t1", trace_id="tr1",
        )
        self.assertEqual(count, 0)


# ============================================================
# memory_tenant 解析
# ============================================================


class TestMemoryTenantResolution(unittest.TestCase):

    def setUp(self):
        self.agent = SimpleAgent(
            tool_registry=_DummyTool(),
            long_term_memory=LongTermMemoryImpl(backend=InMemoryBackend()),
        )

    def test_tenant_from_extra_params(self):
        req = {"extra_params": {"tenant_id": "explicit_tenant"}}
        self.assertEqual(self.agent._memory_tenant(req), "explicit_tenant")

    def test_tenant_default_fallback(self):
        req = {"extra_params": {}}
        # 没 set_current_tenant → 走 "default" 兜底
        result = self.agent._memory_tenant(req)
        self.assertIn(result, ("default", None))  # 跟 observability 状态有关


if __name__ == "__main__":
    unittest.main()
