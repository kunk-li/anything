# -*- coding: utf-8 -*-
"""模型分级路由 (执行计划③) 在 LLMService 侧: generate/chat_stream 读 model_routing ContextVar。

用 unbound method + fake self (含 adapters + call_llm) 测路由决策, 不做重型 LLMService 初始化。
"""
import unittest

from llm_adapter_module.core.impl import LLMService
from observability_module import set_model_routing, reset_model_routing


class _Resp:
    code = "SUCCESS"
    chat_result = "ok"


class _FakeSvc:
    """最小 fake: 只提供 generate 路由逻辑用到的 adapters + call_llm。"""
    def __init__(self):
        self.adapters = {"qwen-plus": object(), "qwen-max": object()}
        self.last = None

    def call_llm(self, request):
        self.last = request
        return _Resp()


class TestGenerateRouting(unittest.TestCase):
    def test_routed_model_and_token_budget_applied(self):
        svc = _FakeSvc()
        tok = set_model_routing("qwen-plus", 123)
        try:
            out = LLMService.generate(svc, "hi")    # unbound; self=svc
        finally:
            reset_model_routing(tok)
        self.assertEqual(out, "ok")
        self.assertEqual(svc.last.model_name, "qwen-plus")
        self.assertEqual(svc.last.model_param.max_tokens, 123)

    def test_unregistered_model_falls_back_to_default(self):
        svc = _FakeSvc()
        tok = set_model_routing("qwen-nonexist", None)
        try:
            LLMService.generate(svc, "hi")
        finally:
            reset_model_routing(tok)
        self.assertEqual(svc.last.model_name, "default")   # 未注册 → 回退 default (fail-safe)

    def test_no_routing_uses_default(self):
        svc = _FakeSvc()
        LLMService.generate(svc, "hi")                      # 无 ctx var
        self.assertEqual(svc.last.model_name, "default")
        self.assertEqual(svc.last.model_param.max_tokens, 4096)   # LLMParam 默认


if __name__ == "__main__":
    unittest.main()
