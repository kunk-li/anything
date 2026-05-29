# -*- coding: utf-8 -*-
"""
compress_history — MemGPT working/archival 分层 (Task JJJ #96).
"""
import unittest
from unittest.mock import MagicMock

from long_term_memory_module import LongTermMemoryImpl
from state_backend_module import InMemoryBackend


class _MockLLM:
    """Mock LLM for compress_history. generate() returns a queue of responses."""
    def __init__(self, responses):
        if isinstance(responses, str):
            responses = [responses]
        self._queue = list(responses)
        self.calls = []

    def generate(self, prompt: str, **kwargs) -> str:
        self.calls.append(prompt)
        if not self._queue:
            return ""
        if len(self._queue) == 1:
            return self._queue[0]
        return self._queue.pop(0)


def _msgs(n: int, role_cycle=("user", "assistant")):
    """生成 n 条假 messages."""
    return [
        {"role": role_cycle[i % 2], "content": f"消息 {i} 内容内容内容"}
        for i in range(n)
    ]


class TestCompressHistoryBypass(unittest.TestCase):
    """不够触发条件 → 原样返回."""

    def setUp(self):
        self.backend = InMemoryBackend()
        self.llm = _MockLLM("[summary irrelevant]")
        self.memory = LongTermMemoryImpl(backend=self.backend, llm_client=self.llm)

    def test_empty_messages(self):
        out, s = self.memory.compress_history([])
        self.assertEqual(out, [])
        self.assertIsNone(s)

    def test_below_threshold_returns_unchanged(self):
        msgs = _msgs(3)
        out, s = self.memory.compress_history(msgs, keep_recent=5)
        self.assertEqual(out, msgs)
        self.assertIsNone(s)
        # LLM 不该被调
        self.assertEqual(len(self.llm.calls), 0)

    def test_equal_keep_recent_returns_unchanged(self):
        msgs = _msgs(5)
        out, s = self.memory.compress_history(msgs, keep_recent=5)
        # 5 条 + keep_recent=5 → len <= keep_recent+2 == 7, 不触发
        self.assertEqual(out, msgs)
        self.assertIsNone(s)


class TestCompressHistoryTrigger(unittest.TestCase):
    """超过阈值 → 走 LLM 总结 + 保留 keep_recent 条."""

    def setUp(self):
        self.backend = InMemoryBackend()
        self.llm = _MockLLM("用户问 RAG 概念, 助手已经回答了基本流程 + 给了示例。")
        self.memory = LongTermMemoryImpl(backend=self.backend, llm_client=self.llm)

    def test_message_count_triggers(self):
        # 10 条, keep_recent=3 → 触发 (10 > 3+2)
        msgs = _msgs(10)
        out, summary = self.memory.compress_history(
            msgs, keep_recent=3, archive_facts=False,
        )
        # 输出 = [summary_system, ...3 recent] = 4 条
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0]["role"], "system")
        self.assertIn("[历史对话摘要", out[0]["content"])
        self.assertIn("老 7 轮已压缩", out[0]["content"])
        self.assertIn("RAG", out[0]["content"])
        # 保留最近 3 条
        self.assertEqual(out[1:], msgs[-3:])
        self.assertIsNotNone(summary)

    def test_total_chars_triggers(self):
        # 6 条 (不超 keep_recent+2), 但每条 3000 char → total 18000 超 max_total_chars=10000
        msgs = [
            {"role": "user", "content": "x" * 3000},
            {"role": "assistant", "content": "y" * 3000},
            {"role": "user", "content": "z" * 3000},
            {"role": "assistant", "content": "a" * 3000},
            {"role": "user", "content": "b" * 3000},
            {"role": "assistant", "content": "c" * 3000},
        ]
        out, summary = self.memory.compress_history(
            msgs, keep_recent=2, max_total_chars=10000, archive_facts=False,
        )
        # 应该触发
        self.assertEqual(out[0]["role"], "system")
        self.assertEqual(len(out), 3)  # 1 summary + 2 recent

    def test_summary_truncated_to_2000(self):
        long_summary = "a" * 5000
        self.memory._llm_client = _MockLLM(long_summary)
        msgs = _msgs(10)
        out, summary = self.memory.compress_history(
            msgs, keep_recent=3, archive_facts=False,
        )
        self.assertLessEqual(len(summary), 2000)


class TestCompressHistoryLLMFailure(unittest.TestCase):
    """LLM 失败 → 退化截断保留最近 keep_recent 条."""

    def test_llm_raises_falls_back_to_truncate(self):
        llm = MagicMock()
        llm.generate.side_effect = RuntimeError("LLM down")
        backend = InMemoryBackend()
        memory = LongTermMemoryImpl(backend=backend, llm_client=llm)
        msgs = _msgs(10)
        out, summary = memory.compress_history(msgs, keep_recent=3, archive_facts=False)
        # 退化 = 只保留最近 3 条, 无 summary system message
        self.assertEqual(len(out), 3)
        self.assertEqual(out, msgs[-3:])
        self.assertIsNone(summary)

    def test_no_llm_client_falls_back(self):
        memory = LongTermMemoryImpl(backend=InMemoryBackend())  # 无 LLM
        msgs = _msgs(10)
        out, summary = memory.compress_history(msgs, keep_recent=3, archive_facts=False)
        self.assertEqual(out, msgs[-3:])
        self.assertIsNone(summary)


class TestCompressHistoryArchiveFacts(unittest.TestCase):
    """archive_facts=True 时把老对话的事实抽到 long_term_memory."""

    def test_facts_extracted_and_stored(self):
        """LLM 第 1 次调 (extract) 给 fact list, 第 2 次调 (summarize) 给 summary."""
        responses = [
            # extract_facts 用
            '[{"content": "用户偏好 Python", "tags": ["preference"], "confidence": 0.9}]',
            # summary 用
            "对话围绕 Python 偏好展开。",
        ]
        llm = _MockLLM(responses)
        memory = LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=llm)
        msgs = _msgs(10)
        out, summary = memory.compress_history(
            msgs, keep_recent=3, tenant_id="t1", archive_facts=True,
        )
        # fact 应被存入 long_term_memory
        stored = memory.list_facts("t1")
        contents = [f.content for f in stored]
        self.assertIn("用户偏好 Python", contents)
        # summary 也产生
        self.assertIsNotNone(summary)

    def test_archive_facts_false_skips_extraction(self):
        llm = _MockLLM("纯总结")
        memory = LongTermMemoryImpl(backend=InMemoryBackend(), llm_client=llm)
        msgs = _msgs(10)
        out, summary = memory.compress_history(
            msgs, keep_recent=3, tenant_id="t1", archive_facts=False,
        )
        # 库里没 fact (没调 extract)
        self.assertEqual(memory.list_facts("t1"), [])


if __name__ == "__main__":
    unittest.main()
