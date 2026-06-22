# -*- coding: utf-8 -*-
"""
SimpleAgent 工具结果缓存测试 (Task FF #66)
"""

import unittest
from agent_module.core.impl import SimpleAgent


class _DictRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, name, func, description=""):
        self._tools[name] = func

    def get(self, name):
        return self._tools.get(name)

    def list_tools(self):
        return list(self._tools.keys())


class _CountingTool:
    """记每次调用次数 + 入参的工具桩"""

    def __init__(self, success: bool = True):
        self.calls = 0
        self.inputs = []
        self._success = success

    def __call__(self, payload):
        self.calls += 1
        self.inputs.append(dict(payload))
        if self._success:
            return {"code": "SUCCESS", "data": {"answer": f"call#{self.calls}: {payload.get('q', '')}"}}
        return {"code": "RAG_RUN_FAILED", "message": "boom"}


def _make_agent(cacheable=None, cache_size=10):
    reg = _DictRegistry()
    agent = SimpleAgent(tool_registry=reg)
    if cacheable is not None:
        agent.cacheable_tools = set(cacheable)
    agent.tool_cache_max_size = cache_size
    return agent, reg


class TestToolCache(unittest.TestCase):

    def test_cache_hit_skips_tool_call(self):
        """相同 input 第二次应命中缓存, 不再触发工具."""
        agent, reg = _make_agent(cacheable=["rag_search"])
        tool = _CountingTool()
        reg.register("rag_search", tool)

        res1 = agent._call_tool_with_retry(
            step={"step_id": "s1", "tool_name": "rag_search", "input_data": {"q": "x"}},
            session_id="ss", trace_id="t1", max_retries=0,
        )
        res2 = agent._call_tool_with_retry(
            step={"step_id": "s2", "tool_name": "rag_search", "input_data": {"q": "x"}},
            session_id="ss", trace_id="t2", max_retries=0,
        )
        self.assertEqual(tool.calls, 1)  # 第二次命中缓存, 工具只调 1 次
        self.assertEqual(res1["success"], True)
        self.assertEqual(res2["success"], True)
        # cache hit 应该标 _cache_hit
        self.assertTrue(res2.get("_cache_hit"))
        # 但 step_id 应是本次的, 不是缓存里的
        self.assertEqual(res2["step_id"], "s2")

    def test_cache_miss_different_input(self):
        """不同 input 走不同缓存, 工具被调 2 次"""
        agent, reg = _make_agent(cacheable=["rag_search"])
        tool = _CountingTool()
        reg.register("rag_search", tool)

        agent._call_tool_with_retry(
            step={"step_id": "s1", "tool_name": "rag_search", "input_data": {"q": "x"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        agent._call_tool_with_retry(
            step={"step_id": "s2", "tool_name": "rag_search", "input_data": {"q": "y"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        self.assertEqual(tool.calls, 2)

    def test_cache_ignores_transient_fields(self):
        """trace_id / session_id 不同时仍应命中缓存"""
        agent, reg = _make_agent(cacheable=["rag_search"])
        tool = _CountingTool()
        reg.register("rag_search", tool)

        agent._call_tool_with_retry(
            step={"step_id": "s1", "tool_name": "rag_search",
                  "input_data": {"q": "x", "trace_id": "t1", "session_id": "A"}},
            session_id="A", trace_id="t1", max_retries=0,
        )
        agent._call_tool_with_retry(
            step={"step_id": "s2", "tool_name": "rag_search",
                  "input_data": {"q": "x", "trace_id": "t2", "session_id": "B"}},
            session_id="B", trace_id="t2", max_retries=0,
        )
        self.assertEqual(tool.calls, 1)

    def test_non_cacheable_tool_always_runs(self):
        """没在 cacheable_tools 名单里的工具每次都跑"""
        agent, reg = _make_agent(cacheable=["other_tool"])
        tool = _CountingTool()
        reg.register("rag_search", tool)

        for _ in range(3):
            agent._call_tool_with_retry(
                step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": "x"}},
                session_id="ss", trace_id="t", max_retries=0,
            )
        self.assertEqual(tool.calls, 3)  # 没缓存, 3 次都跑

    def test_failed_results_not_cached(self):
        """失败的工具调用不应该缓存 (否则把暂时故障锁死)"""
        agent, reg = _make_agent(cacheable=["rag_search"])
        tool = _CountingTool(success=False)
        reg.register("rag_search", tool)

        for _ in range(3):
            agent._call_tool_with_retry(
                step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": "x"}},
                session_id="ss", trace_id="t", max_retries=0,
            )
        self.assertEqual(tool.calls, 3)  # 每次都跑

    def test_lru_eviction(self):
        """超过 max_size 时, 最早的 key 被淘汰"""
        agent, reg = _make_agent(cacheable=["rag_search"], cache_size=2)
        tool = _CountingTool()
        reg.register("rag_search", tool)

        # 放 3 个 key
        for q in ["a", "b", "c"]:
            agent._call_tool_with_retry(
                step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": q}},
                session_id="ss", trace_id="t", max_retries=0,
            )
        self.assertEqual(tool.calls, 3)

        # cache 现在应该有 b, c (a 被淘汰)
        # 再 query a → cache miss, 工具再跑
        agent._call_tool_with_retry(
            step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": "a"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        self.assertEqual(tool.calls, 4)
        # 再 query c → cache hit (c 还在)
        agent._call_tool_with_retry(
            step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": "c"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        self.assertEqual(tool.calls, 4)

    def test_lru_recency_update(self):
        """命中缓存后 key 应移到队尾 (LRU 更新): 后续淘汰应淘汰其他更旧的"""
        agent, reg = _make_agent(cacheable=["rag_search"], cache_size=2)
        tool = _CountingTool()
        reg.register("rag_search", tool)

        # 放 a 和 b
        agent._call_tool_with_retry(
            step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": "a"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        agent._call_tool_with_retry(
            step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": "b"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        # 命中 a (move-to-end)
        agent._call_tool_with_retry(
            step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": "a"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        # 添加 c → 淘汰 b (因为 a 刚被访问)
        agent._call_tool_with_retry(
            step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": "c"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        # b 应该被淘汰: 再 query b → miss
        before = tool.calls
        agent._call_tool_with_retry(
            step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": "b"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        self.assertEqual(tool.calls, before + 1)  # miss

    def test_stats(self):
        agent, reg = _make_agent(cacheable=["rag_search"])
        tool = _CountingTool()
        reg.register("rag_search", tool)

        agent._call_tool_with_retry(
            step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": "x"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        agent._call_tool_with_retry(
            step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": "x"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        stats = agent.tool_cache_stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["hit_ratio"], 0.5)
        self.assertEqual(stats["size"], 1)

    def test_datetime_not_in_default_cacheable(self):
        """datetime 的输出随墙钟变化 (op=now), 不是入参的纯函数, 必须不在默认可缓存名单里。
        否则同一 (op=now) 入参的 cache key 永远命中首次结果 → '现在几点' 被冻结。"""
        agent = SimpleAgent(tool_registry=_DictRegistry())
        self.assertNotIn("datetime", agent.cacheable_tools)
        # 对照: rag_search 这类纯只读工具仍应默认可缓存
        self.assertIn("rag_search", agent.cacheable_tools)

    def test_now_sensitive_tool_runs_every_time(self):
        """模拟 datetime/now 工具: 不在 cacheable_tools → 每次都实际执行, 返回新鲜时间。"""
        agent, reg = _make_agent(cacheable=None)  # 用默认 cacheable 名单
        agent.cacheable_tools = set(SimpleAgent(tool_registry=_DictRegistry()).cacheable_tools)
        calls = {"n": 0}

        def _now(payload):
            calls["n"] += 1
            return {"code": "SUCCESS", "data": {"iso": f"2026-06-22T10:0{calls['n']}:00"}}

        reg.register("datetime", _now)
        out1 = agent._call_tool_with_retry(
            step={"step_id": "s1", "tool_name": "datetime", "input_data": {"op": "now"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        out2 = agent._call_tool_with_retry(
            step={"step_id": "s2", "tool_name": "datetime", "input_data": {"op": "now"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        self.assertEqual(calls["n"], 2)  # 每次都真跑, 没被缓存冻结
        self.assertNotEqual(out1["output"]["data"]["iso"], out2["output"]["data"]["iso"])

    def test_clear_cache(self):
        agent, reg = _make_agent(cacheable=["rag_search"])
        tool = _CountingTool()
        reg.register("rag_search", tool)
        agent._call_tool_with_retry(
            step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": "x"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        agent.clear_tool_cache()
        # clear 后再 query 应该 miss
        agent._call_tool_with_retry(
            step={"step_id": "s", "tool_name": "rag_search", "input_data": {"q": "x"}},
            session_id="ss", trace_id="t", max_retries=0,
        )
        self.assertEqual(tool.calls, 2)  # cache 清空, 第二次也实际跑了


if __name__ == "__main__":
    unittest.main()
