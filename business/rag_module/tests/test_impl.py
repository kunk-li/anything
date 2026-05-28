# -*- coding: utf-8 -*-
"""
RAG 模块单元测试

覆盖核心功能、签名契约、异常路径。

设计说明:
    本测试不真实初始化向量库/文档存储,因此 retrieve / run 调用主要验证
    "请求/响应结构契约",而不验证检索质量(后者由 smoke test 端到端验证)。
"""

import unittest

# 绝对 import,避免 unittest discover 时相对 import 失败
from rag_module.core.impl import SimpleRAG


class MockLLMClient:
    """模拟大模型客户端"""

    def generate(self, prompt, trace_id=None):
        return "这是模拟的 RAG 回答。"


class TestSimpleRAG(unittest.TestCase):
    """RAG 模块单元测试"""

    def setUp(self):
        # 仅注入 llm_client,其他依赖保持为 None
        # (retrieve 在 vector_db=None 时直接返回 [],不会报错)
        self.llm = MockLLMClient()
        self.rag = SimpleRAG(llm_client=self.llm)

    def test_retrieve_with_no_vector_db_returns_empty(self):
        """没有 vector_db 时 retrieve 应返回空列表而非崩溃。"""
        chunks = self.rag.retrieve({
            "query": "什么是 RAG?",
            "top_k": 3,
            "trace_id": "t1",
        })
        self.assertIsInstance(chunks, list)
        self.assertEqual(chunks, [])

    def test_run_returns_unified_envelope(self):
        """run 应返回统一响应信封 (code/message/data/trace_id/...)。"""
        result = self.rag.run({
            "query": "什么是 RAG?",
            "top_k": 3,
            "trace_id": "t1",
        })
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["trace_id"], "t1")
        self.assertIn("answer", result["data"])
        self.assertIn("citations", result["data"])
        self.assertIn("retrieved_chunks", result["data"])

    def test_run_empty_query_still_returns_envelope(self):
        """空 query 在 RAG 层不抛异常(校验由 RequestHandler 边界负责),
        但应能返回明确的响应结构。"""
        result = self.rag.run({
            "query": "",
            "top_k": 3,
            "trace_id": "t1",
        })
        # 不论成功失败,都必须返回统一信封字段
        self.assertIn("code", result)
        self.assertIn("trace_id", result)

    def test_call_rag_keyword_interface(self):
        """call_rag 关键字参数风格入口(BaseRAG 契约)。"""
        result = self.rag.call_rag(
            query="什么是 RAG?",
            top_k=3,
            trace_id="t1",
            session_id="s1",
        )
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["trace_id"], "t1")


if __name__ == "__main__":
    unittest.main()


# ==========================================
# Task #46 — 会话记忆
# ==========================================
import unittest as _ut


class _MemStateStore:
    """内存版 state_store, 跟 LocalStateStore 同接口"""
    def __init__(self):
        self.data = {}
    def get_state(self, session_id):
        return self.data.get(session_id)
    def append_event(self, session_id, event):
        st = self.data.setdefault(session_id, {"events": []})
        st["events"].append(dict(event))
        return True
    def save_state(self, session_id, state):
        self.data[session_id] = dict(state)
        return True


class TestRAGHistoryMemory(_ut.TestCase):
    """RAG 会话记忆 _load_history / _save_turn / _build_prompt 集成"""

    def _make_rag(self, history_max_turns=6):
        store = _MemStateStore()
        rag = SimpleRAG(
            llm_client=MockLLMClient(),
            state_store=store,
        )
        rag.history_max_turns = history_max_turns
        return rag, store

    def test_history_empty_when_no_session(self):
        rag, _ = self._make_rag()
        self.assertEqual(rag._load_history(None), [])
        self.assertEqual(rag._load_history(""), [])

    def test_history_empty_when_no_state_store(self):
        rag, _ = self._make_rag()
        rag.state_store = None
        self.assertEqual(rag._load_history("sess_x"), [])

    def test_save_then_load_round_trip(self):
        rag, store = self._make_rag()
        rag._save_turn("sess_x", "你好", "你好啊!")
        rag._save_turn("sess_x", "几点了?", "中午 12 点")
        hist = rag._load_history("sess_x")
        self.assertEqual(len(hist), 4)
        self.assertEqual(hist[0], {"role": "user", "content": "你好"})
        self.assertEqual(hist[1], {"role": "assistant", "content": "你好啊!"})
        self.assertEqual(hist[2], {"role": "user", "content": "几点了?"})
        self.assertEqual(hist[3], {"role": "assistant", "content": "中午 12 点"})

    def test_max_turns_truncates(self):
        rag, _ = self._make_rag(history_max_turns=2)
        for i in range(5):
            rag._save_turn("s1", f"q{i}", f"a{i}")
        hist = rag._load_history("s1")
        # 最多 2 轮 = 4 条
        self.assertEqual(len(hist), 4)
        # 应该是最近 2 轮 (q3/a3, q4/a4)
        self.assertEqual(hist[0]["content"], "q3")
        self.assertEqual(hist[-1]["content"], "a4")

    def test_disabled_when_max_turns_zero(self):
        rag, _ = self._make_rag(history_max_turns=0)
        rag._save_turn("s2", "q", "a")
        self.assertEqual(rag._load_history("s2"), [])

    def test_history_isolated_by_session_id(self):
        rag, _ = self._make_rag()
        rag._save_turn("sA", "qA", "aA")
        rag._save_turn("sB", "qB", "aB")
        self.assertEqual(len(rag._load_history("sA")), 2)
        self.assertEqual(len(rag._load_history("sB")), 2)
        self.assertEqual(rag._load_history("sA")[0]["content"], "qA")
        self.assertEqual(rag._load_history("sB")[0]["content"], "qB")

    def test_build_prompt_includes_history(self):
        history = [
            {"role": "user", "content": "刚刚问了什么"},
            {"role": "assistant", "content": "天气"},
        ]
        rag, _ = self._make_rag()
        prompt = rag._build_prompt(
            query="再问一次", context_chunks=[], history=history
        )
        self.assertIn("历史对话", prompt)
        self.assertIn("刚刚问了什么", prompt)
        self.assertIn("再问一次", prompt)

    def test_build_prompt_no_history_block_when_empty(self):
        rag, _ = self._make_rag()
        prompt = rag._build_prompt(
            query="hello", context_chunks=[], history=None
        )
        self.assertNotIn("历史对话", prompt)

    def test_state_store_failure_silent(self):
        """state_store 抛异常不应中断 RAG (只是 WARN 日志)"""
        class _BadStore:
            def get_state(self, sid): raise RuntimeError("disk full")
            def append_event(self, sid, ev): raise RuntimeError("disk full")
        rag, _ = self._make_rag()
        rag.state_store = _BadStore()
        # 不抛异常
        self.assertEqual(rag._load_history("sess"), [])
        rag._save_turn("sess", "q", "a")  # 不抛
