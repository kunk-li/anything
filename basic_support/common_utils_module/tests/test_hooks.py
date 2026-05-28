# -*- coding: utf-8 -*-
"""
Hooks 系统单元测试 (Task Z #60)

覆盖:
    - 4 类 event 注册 + 触发
    - hook 修改 input/result 传递给下一个 hook
    - BlockedError 中断 + 往上传
    - 普通异常被吞, 链路继续
    - clear / count / list 内省
    - 单例 + reset
    - 装饰器写法
"""

import unittest

from common_utils_module import (
    BlockedError,
    HookRegistry,
    get_hook_registry,
    reset_hook_registry,
)


class TestHookRegistry(unittest.TestCase):
    def setUp(self):
        reset_hook_registry()

    def tearDown(self):
        reset_hook_registry()

    def test_add_and_fire_pre_tool_call(self):
        reg = HookRegistry()
        called = []
        def my_hook(tool_name, input_data, ctx):
            called.append((tool_name, input_data, ctx))
        reg.add_pre_tool_call(my_hook)
        reg.fire("pre_tool_call", "rag_search", {"q": "hi"}, {"trace_id": "t1"})
        self.assertEqual(len(called), 1)
        self.assertEqual(called[0][0], "rag_search")

    def test_hook_modifies_input(self):
        """返回 dict 应该被下一个 hook + 主链路当作新 input."""
        reg = HookRegistry()
        @reg.pre_tool_call
        def add_default_top_k(tool_name, input_data, ctx):
            new = dict(input_data)
            new.setdefault("top_k", 5)
            return new
        result = reg.fire("pre_tool_call", "rag_search", {"q": "x"}, {})
        self.assertEqual(result, {"q": "x", "top_k": 5})

    def test_hook_chain_passes_modified_input(self):
        """两个 hook 串联: 第一个加 top_k, 第二个把 q 转大写"""
        reg = HookRegistry()
        @reg.pre_tool_call
        def h1(tool_name, input_data, ctx):
            new = dict(input_data); new["top_k"] = 5
            return new
        @reg.pre_tool_call
        def h2(tool_name, input_data, ctx):
            new = dict(input_data); new["q"] = (new.get("q") or "").upper()
            return new
        # fire 的 args 是不变的; 但每个 hook 接收上一个的 return 的责任由调用方实现.
        # 这里我们只测试 result 是最后一个非 None 返回值
        result = reg.fire("pre_tool_call", "rag_search", {"q": "abc"}, {})
        self.assertEqual(result.get("top_k"), 5)
        self.assertEqual(result.get("q"), "ABC")

    def test_blocked_error_propagates(self):
        reg = HookRegistry()
        @reg.pre_tool_call
        def deny_dangerous(tool_name, input_data, ctx):
            if tool_name == "py_sandbox":
                raise BlockedError("py_sandbox blocked by policy", code="POLICY_DENY")
        with self.assertRaises(BlockedError) as cm:
            reg.fire("pre_tool_call", "py_sandbox", {}, {})
        self.assertEqual(cm.exception.code, "POLICY_DENY")

    def test_normal_exception_swallowed(self):
        """一个 hook 抛非 BlockedError → 跳过它, 后续 hook 仍跑"""
        reg = HookRegistry()
        called = []
        @reg.pre_tool_call
        def bad(tool_name, input_data, ctx):
            raise RuntimeError("boom")
        @reg.pre_tool_call
        def good(tool_name, input_data, ctx):
            called.append(tool_name)
        reg.fire("pre_tool_call", "x", {}, {})
        self.assertEqual(called, ["x"])

    def test_post_tool_call_sees_output(self):
        reg = HookRegistry()
        captured = {}
        @reg.post_tool_call
        def see(tool_name, input_data, output, ctx):
            captured["tool"] = tool_name
            captured["output"] = output
        reg.fire("post_tool_call", "calc", {"x": 1}, {"code": "SUCCESS", "data": 42}, {})
        self.assertEqual(captured["tool"], "calc")
        self.assertEqual(captured["output"]["data"], 42)

    def test_pre_post_llm_call(self):
        reg = HookRegistry()
        @reg.pre_llm_call
        def add_prefix(prompt, model, ctx):
            return "[prefixed]\n" + prompt
        @reg.post_llm_call
        def measure(prompt, model, response, ctx):
            ctx["llm_len"] = len(response or "")
        new_prompt = reg.fire("pre_llm_call", "hello", "gpt-4o-mini", {})
        self.assertEqual(new_prompt, "[prefixed]\nhello")
        ctx = {}
        reg.fire("post_llm_call", "hi", "gpt-4o-mini", "world", ctx)
        self.assertEqual(ctx.get("llm_len"), 5)

    def test_unknown_event_raises(self):
        reg = HookRegistry()
        with self.assertRaises(ValueError):
            reg.add("not_real_event", lambda *a, **kw: None)

    def test_unknown_event_fire_returns_none(self):
        reg = HookRegistry()
        self.assertIsNone(reg.fire("never_heard_of_it"))

    def test_count_and_list(self):
        reg = HookRegistry()
        @reg.pre_tool_call
        def a(tool_name, input_data, ctx): pass
        @reg.pre_tool_call
        def b(tool_name, input_data, ctx): pass
        @reg.post_llm_call
        def c(prompt, model, response, ctx): pass
        self.assertEqual(reg.count()["pre_tool_call"], 2)
        self.assertEqual(reg.count()["post_llm_call"], 1)
        listed = reg.list()
        self.assertEqual(set(listed["pre_tool_call"]), {"a", "b"})

    def test_clear_specific_event(self):
        reg = HookRegistry()
        @reg.pre_tool_call
        def x(tool_name, input_data, ctx): pass
        @reg.post_tool_call
        def y(tool_name, input_data, output, ctx): pass
        reg.clear("pre_tool_call")
        self.assertEqual(reg.count()["pre_tool_call"], 0)
        self.assertEqual(reg.count()["post_tool_call"], 1)

    def test_singleton(self):
        a = get_hook_registry()
        b = get_hook_registry()
        self.assertIs(a, b)
        reset_hook_registry()
        c = get_hook_registry()
        self.assertIsNot(a, c)


if __name__ == "__main__":
    unittest.main()
