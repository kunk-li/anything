# -*- coding: utf-8 -*-
"""单测 — Task #35 新增 LLM 适配器: Anthropic + Ollama (mocked HTTP)"""

import os
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

import unittest
from unittest.mock import MagicMock, patch

from llm_adapter_module.core.impl import AnthropicChatAdapter, OllamaChatAdapter
from llm_adapter_module.model.data_model import LLMRequest, LLMParam


def _common_cfg():
    return {"timeout": 5, "max_retry": 1}


class TestAnthropicChatAdapter(unittest.TestCase):

    def test_mock_when_no_key(self):
        """未配 api_key 时返回 mock 回复, 不发请求"""
        adapter = AnthropicChatAdapter(
            model_name="claude-3-haiku",
            model_cfg={},  # no api_key
            common_cfg=_common_cfg(),
            logger=MagicMock(),
        )
        self.assertFalse(adapter.check_config())
        out = adapter.generate("hi", LLMRequest(request_type="CHAT", model_param=LLMParam(max_tokens=128)))
        self.assertIn("mock-anthropic", out)
        self.assertIn("claude-3-haiku", out)

    def test_payload_shape(self):
        """有 api_key 时, _post_json 应被以 anthropic 格式调用"""
        adapter = AnthropicChatAdapter(
            model_name="claude-3-sonnet",
            model_cfg={"api_key": "sk-ant-xxx", "api_base": "https://api.anthropic.com"},
            common_cfg=_common_cfg(),
            logger=MagicMock(),
        )
        captured = {}

        def _fake_post(url, headers, payload, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            return {"content": [{"type": "text", "text": "Hi there!"}]}

        with patch.object(adapter, "_post_json", side_effect=_fake_post):
            out = adapter.chat_with_context(
                [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Who are you?"},
                ],
                LLMRequest(request_type="CHAT", model_param=LLMParam(max_tokens=256, temperature=0.3)),
            )

        self.assertEqual(out, "Hi there!")
        # endpoint
        self.assertTrue(captured["url"].endswith("/v1/messages"))
        # header 是 x-api-key 不是 Authorization
        self.assertEqual(captured["headers"]["x-api-key"], "sk-ant-xxx")
        self.assertIn("anthropic-version", captured["headers"])
        # system 应被拆出来到顶层
        self.assertEqual(captured["payload"]["system"], "You are helpful.")
        # messages 不应再含 system role
        roles = [m["role"] for m in captured["payload"]["messages"]]
        self.assertNotIn("system", roles)
        # max_tokens 必传
        self.assertEqual(captured["payload"]["max_tokens"], 256)


class TestOllamaChatAdapter(unittest.TestCase):

    def test_no_auth_required(self):
        """Ollama 本地 API 没配 key 也算 configured (因为 api_base 默认本机)"""
        adapter = OllamaChatAdapter(
            model_name="qwen2.5:7b",
            model_cfg={},  # no api_key
            common_cfg=_common_cfg(),
            logger=MagicMock(),
        )
        # default api_base = http://localhost:11434, 所以 check_config 返回 True
        self.assertTrue(adapter.check_config())

    def test_payload_shape(self):
        adapter = OllamaChatAdapter(
            model_name="llama3.2:3b",
            model_cfg={"api_base": "http://localhost:11434"},
            common_cfg=_common_cfg(),
            logger=MagicMock(),
        )
        captured = {}

        def _fake_post(url, headers, payload, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            return {"message": {"role": "assistant", "content": "Hello world"}, "done": True}

        with patch.object(adapter, "_post_json", side_effect=_fake_post):
            out = adapter.generate(
                "ping",
                LLMRequest(request_type="CHAT", model_param=LLMParam(temperature=0.5, max_tokens=64)),
            )

        self.assertEqual(out, "Hello world")
        self.assertTrue(captured["url"].endswith("/api/chat"))
        self.assertEqual(captured["payload"]["stream"], False)
        self.assertEqual(captured["payload"]["options"]["temperature"], 0.5)
        self.assertEqual(captured["payload"]["options"]["num_predict"], 64)
        # 没配 api_key 时 Authorization header 不应出现
        self.assertNotIn("Authorization", captured["headers"])

    def test_with_bearer_token(self):
        """配了 api_key 时应当带 Authorization header (反代场景)"""
        adapter = OllamaChatAdapter(
            model_name="qwen2.5",
            model_cfg={"api_key": "proxy-token", "api_base": "http://proxy:8080"},
            common_cfg=_common_cfg(),
            logger=MagicMock(),
        )
        captured = {}
        with patch.object(adapter, "_post_json",
                          side_effect=lambda u, h, p, timeout: captured.update({"h": h}) or
                                                                {"message": {"content": "x"}}):
            adapter.generate("hi", LLMRequest(request_type="CHAT", model_param=LLMParam()))
        self.assertEqual(captured["h"]["Authorization"], "Bearer proxy-token")


class TestLLMServiceRegistersNewAdapters(unittest.TestCase):
    """LLMService._build_adapter mapping 必须能识别新 adapter_class 字符串"""

    def test_anthropic_in_mapping(self):
        from llm_adapter_module.core.impl import LLMService
        svc = LLMService()
        adapter = svc._build_adapter(
            adapter_class="AnthropicChatAdapter",
            model_name="claude-3-haiku",
            model_cfg={"api_key": "sk-x", "api_base": "https://api.anthropic.com"},
            common_cfg={"timeout": 5, "max_retry": 1},
        )
        self.assertIsNotNone(adapter)
        self.assertEqual(type(adapter).__name__, "AnthropicChatAdapter")

    def test_ollama_in_mapping(self):
        from llm_adapter_module.core.impl import LLMService
        svc = LLMService()
        adapter = svc._build_adapter(
            adapter_class="OllamaChatAdapter",
            model_name="qwen2.5",
            model_cfg={"api_base": "http://localhost:11434"},
            common_cfg={"timeout": 5, "max_retry": 1},
        )
        self.assertIsNotNone(adapter)
        self.assertEqual(type(adapter).__name__, "OllamaChatAdapter")


if __name__ == "__main__":
    unittest.main()
