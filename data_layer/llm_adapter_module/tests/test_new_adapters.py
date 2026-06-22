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

    def test_roleless_message_defaults_to_user(self):
        """非 system 消息缺 role 时不应 KeyError, 默认补 user"""
        adapter = AnthropicChatAdapter(
            model_name="claude-3-sonnet",
            model_cfg={"api_key": "sk-ant-xxx", "api_base": "https://api.anthropic.com"},
            common_cfg=_common_cfg(),
            logger=MagicMock(),
        )
        captured = {}

        def _fake_post(url, headers, payload, timeout):
            captured["payload"] = payload
            return {"content": [{"type": "text", "text": "ok"}]}

        with patch.object(adapter, "_post_json", side_effect=_fake_post):
            out = adapter.chat_with_context(
                [{"content": "no role here"}],  # 无 role 键
                LLMRequest(request_type="CHAT", model_param=LLMParam(max_tokens=64)),
            )

        self.assertEqual(out, "ok")
        self.assertEqual(captured["payload"]["messages"][0]["role"], "user")
        self.assertEqual(captured["payload"]["messages"][0]["content"], "no role here")


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


class TestStreamingAdapters(unittest.TestCase):
    """Task #45: 三家 chat_stream 真实 token 流"""

    def test_openai_chat_stream_mock_when_no_key(self):
        """OpenAI 没 key 时降级 chat_with_context 切片 (仍是 generator)"""
        from llm_adapter_module.core.impl import OpenAIChatAdapter
        adapter = OpenAIChatAdapter(
            model_name="gpt-4o", model_cfg={},
            common_cfg=_common_cfg(), logger=MagicMock(),
        )
        chunks = list(adapter.chat_stream(
            [{"role": "user", "content": "hi"}],
            LLMRequest(request_type="CHAT", model_param=LLMParam()),
        ))
        self.assertTrue(chunks)
        joined = "".join(chunks)
        self.assertIn("mock-chat", joined)

    def test_openai_chat_stream_sse_parsing(self):
        """OpenAI 有 key 时, 走 SSE 解析"""
        from llm_adapter_module.core.impl import OpenAIChatAdapter
        adapter = OpenAIChatAdapter(
            model_name="gpt-4o",
            model_cfg={"api_key": "sk-x", "api_base": "https://api.x"},
            common_cfg=_common_cfg(), logger=MagicMock(),
        )

        def _fake_stream(url, headers, payload, timeout):
            assert payload.get("stream") is True
            assert payload["model"] == "gpt-4o"
            for tok in ["Hello", ", ", "world", "!"]:
                yield tok

        with patch.object(adapter, "_post_stream_openai", side_effect=_fake_stream):
            tokens = list(adapter.chat_stream(
                [{"role": "user", "content": "hi"}],
                LLMRequest(request_type="CHAT", model_param=LLMParam(max_tokens=100)),
            ))
        self.assertEqual("".join(tokens), "Hello, world!")

    def test_anthropic_chat_stream_payload(self):
        """Anthropic chat_stream payload 包含 stream + system 拆分"""
        from llm_adapter_module.core.impl import AnthropicChatAdapter
        adapter = AnthropicChatAdapter(
            model_name="claude-3-haiku",
            model_cfg={"api_key": "sk-ant", "api_base": "https://api.anthropic.com"},
            common_cfg=_common_cfg(), logger=MagicMock(),
        )
        captured = {}

        def _fake_stream(url, headers, payload, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            for tok in ["A", "n", "thropic"]:
                yield tok

        with patch.object(adapter, "_post_stream_anthropic", side_effect=_fake_stream):
            tokens = list(adapter.chat_stream(
                [
                    {"role": "system", "content": "You are X"},
                    {"role": "user", "content": "Hi"},
                ],
                LLMRequest(request_type="CHAT", model_param=LLMParam(max_tokens=256)),
            ))
        self.assertEqual("".join(tokens), "Anthropic")
        self.assertEqual(captured["payload"]["stream"], True)
        self.assertEqual(captured["payload"]["system"], "You are X")
        self.assertEqual(captured["headers"]["x-api-key"], "sk-ant")
        self.assertEqual(captured["headers"]["Accept"], "text/event-stream")

    def test_ollama_chat_stream_payload(self):
        """Ollama chat_stream payload stream=True, NDJSON"""
        from llm_adapter_module.core.impl import OllamaChatAdapter
        adapter = OllamaChatAdapter(
            model_name="qwen2.5",
            model_cfg={"api_base": "http://localhost:11434"},
            common_cfg=_common_cfg(), logger=MagicMock(),
        )
        captured = {}

        def _fake_stream(url, headers, payload, timeout):
            captured["payload"] = payload
            for tok in ["Q", "wen", " local"]:
                yield tok

        with patch.object(adapter, "_post_stream_ollama", side_effect=_fake_stream):
            tokens = list(adapter.chat_stream(
                [{"role": "user", "content": "hi"}],
                LLMRequest(request_type="CHAT", model_param=LLMParam(temperature=0.5)),
            ))
        self.assertEqual("".join(tokens), "Qwen local")
        self.assertEqual(captured["payload"]["stream"], True)
        self.assertEqual(captured["payload"]["options"]["temperature"], 0.5)


class TestSSEParsers(unittest.TestCase):
    """直接测 _post_stream_openai / _post_stream_anthropic / _post_stream_ollama
    的 SSE / NDJSON 解析逻辑"""

    def _mock_iter_lines(self, lines):
        """mock requests.post 返回响应, iter_lines 吐出指定行"""
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.iter_lines = lambda decode_unicode=True: iter(lines)
        resp.raise_for_status = lambda: None
        return resp

    def test_openai_sse_parser(self):
        from llm_adapter_module.core.impl import _BaseHTTPAdapterMixin
        adapter = _BaseHTTPAdapterMixin()
        lines = [
            'data: {"choices":[{"delta":{"content":"He"}}]}',
            'data: {"choices":[{"delta":{"content":"llo"}}]}',
            '',  # 空行应跳过
            'data: {"choices":[{"delta":{"role":"assistant"}}]}',  # 无 content 跳过
            'data: {"choices":[{"delta":{"content":"!"}}]}',
            'data: [DONE]',
        ]
        with patch("llm_adapter_module.core.adapters._http_mixin.requests.post",
                   return_value=self._mock_iter_lines(lines)):
            tokens = list(adapter._post_stream_openai(
                "http://x", {}, {}, timeout=10,
            ))
        self.assertEqual("".join(tokens), "Hello!")

    def test_anthropic_sse_parser(self):
        from llm_adapter_module.core.impl import _BaseHTTPAdapterMixin
        adapter = _BaseHTTPAdapterMixin()
        # Anthropic 多事件结构, 我们只挑 content_block_delta
        lines = [
            'event: message_start',
            'data: {"type":"message_start","message":{}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"He"}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"llo"}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]
        with patch("llm_adapter_module.core.adapters._http_mixin.requests.post",
                   return_value=self._mock_iter_lines(lines)):
            tokens = list(adapter._post_stream_anthropic(
                "http://x", {}, {}, timeout=10,
            ))
        self.assertEqual("".join(tokens), "Hello")

    def test_ollama_ndjson_parser(self):
        from llm_adapter_module.core.impl import _BaseHTTPAdapterMixin
        adapter = _BaseHTTPAdapterMixin()
        lines = [
            '{"message":{"content":"He"},"done":false}',
            '{"message":{"content":"llo"},"done":false}',
            '',  # 空行跳过
            'not-json',  # 非 JSON 跳过
            '{"message":{"content":"!"},"done":false}',
            '{"message":{"content":""},"done":true}',
        ]
        with patch("llm_adapter_module.core.adapters._http_mixin.requests.post",
                   return_value=self._mock_iter_lines(lines)):
            tokens = list(adapter._post_stream_ollama(
                "http://x", {}, {}, timeout=10,
            ))
        self.assertEqual("".join(tokens), "Hello!")


class TestOpenAIVectorAdapter(unittest.TestCase):
    """embed_batch 必须按 response item['index'] 把向量散列回输入顺序,
    并在响应条目数/向量缺失时抛错触发重试, 而不是静默错配/截短."""

    def _adapter(self, max_retry=1):
        from llm_adapter_module.core.impl import OpenAIVectorAdapter
        return OpenAIVectorAdapter(
            model_name="text-embedding-3-small",
            model_cfg={"api_key": "sk-x", "api_base": "https://api.x"},
            common_cfg={"timeout": 5, "max_retry": max_retry},
            logger=MagicMock(),
        )

    def test_realigns_out_of_order_index(self):
        """API 乱序返回时, 必须按 index 把向量对齐回原输入顺序"""
        adapter = self._adapter()
        texts = ["a", "b", "c"]

        def _fake_post(url, headers, payload, timeout):
            # 故意打乱顺序: index 2 先返回
            return {"data": [
                {"index": 2, "embedding": [3.0]},
                {"index": 0, "embedding": [1.0]},
                {"index": 1, "embedding": [2.0]},
            ]}

        with patch.object(adapter, "_post_json", side_effect=_fake_post):
            out = adapter.embed_batch(texts, LLMRequest(request_type="VECTOR", model_param=LLMParam()))

        self.assertEqual(out, [[1.0], [2.0], [3.0]])

    def test_partial_response_raises(self):
        """返回条目少于输入时抛错 (触发重试/上层回退), 不静默返回短列表"""
        adapter = self._adapter(max_retry=1)
        texts = ["a", "b", "c"]

        def _fake_post(url, headers, payload, timeout):
            return {"data": [
                {"index": 0, "embedding": [1.0]},
                {"index": 1, "embedding": [2.0]},
            ]}

        with patch.object(adapter, "_post_json", side_effect=_fake_post):
            with self.assertRaises(Exception):
                adapter.embed_batch(texts, LLMRequest(request_type="VECTOR", model_param=LLMParam()))

    def test_empty_embedding_raises(self):
        """某条目 embedding 为空也视为部分响应, 抛错"""
        adapter = self._adapter(max_retry=1)
        texts = ["a", "b"]

        def _fake_post(url, headers, payload, timeout):
            return {"data": [
                {"index": 0, "embedding": [1.0]},
                {"index": 1, "embedding": []},
            ]}

        with patch.object(adapter, "_post_json", side_effect=_fake_post):
            with self.assertRaises(Exception):
                adapter.embed_batch(texts, LLMRequest(request_type="VECTOR", model_param=LLMParam()))

    def test_missing_index_falls_back_to_order(self):
        """index 缺失时退回枚举顺序, 正常对齐"""
        adapter = self._adapter()
        texts = ["a", "b"]

        def _fake_post(url, headers, payload, timeout):
            return {"data": [
                {"embedding": [1.0]},
                {"embedding": [2.0]},
            ]}

        with patch.object(adapter, "_post_json", side_effect=_fake_post):
            out = adapter.embed_batch(texts, LLMRequest(request_type="VECTOR", model_param=LLMParam()))

        self.assertEqual(out, [[1.0], [2.0]])


class TestOpenAIMultimodalAdapterMime(unittest.TestCase):
    """data URI 的 MIME 必须按 media_path 扩展名 / metadata 推断, 不能恒为 image/png"""

    def _adapter(self):
        from llm_adapter_module.core.impl import OpenAIMultimodalAdapter
        return OpenAIMultimodalAdapter(
            model_name="gpt-4o",
            model_cfg={"api_key": "sk-x", "api_base": "https://api.x",
                       "support_media": ["image"]},
            common_cfg=_common_cfg(),
            logger=MagicMock(),
        )

    def _capture_urls(self, adapter, media_list):
        captured = {}

        def _fake_post(url, headers, payload, timeout):
            captured["payload"] = payload
            return {"choices": [{"message": {"content": "ok"}}]}

        with patch.object(adapter, "_post_json", side_effect=_fake_post):
            adapter.understand_text_media(
                "看图", media_list,
                LLMRequest(request_type="MULTIMODAL", model_param=LLMParam()),
            )
        content = captured["payload"]["messages"][0]["content"]
        return [c["image_url"]["url"] for c in content if c.get("type") == "image_url"]

    def test_jpeg_extension(self):
        from llm_adapter_module.model.data_model import MediaContent
        m = MediaContent(media_type="image", media_path="/tmp/pic.jpg", media_base64="QQ==")
        urls = self._capture_urls(self._adapter(), [m])
        self.assertTrue(urls[0].startswith("data:image/jpeg;base64,"), urls[0])

    def test_webp_extension_case_insensitive(self):
        from llm_adapter_module.model.data_model import MediaContent
        m = MediaContent(media_type="image", media_path="/tmp/pic.WEBP", media_base64="QQ==")
        urls = self._capture_urls(self._adapter(), [m])
        self.assertTrue(urls[0].startswith("data:image/webp;base64,"), urls[0])

    def test_metadata_mime_wins(self):
        from llm_adapter_module.model.data_model import MediaContent
        m = MediaContent(media_type="image", media_path="/tmp/pic.bin",
                         media_base64="QQ==", media_metadata={"mime": "image/gif"})
        urls = self._capture_urls(self._adapter(), [m])
        self.assertTrue(urls[0].startswith("data:image/gif;base64,"), urls[0])

    def test_metadata_format_fallback(self):
        from llm_adapter_module.model.data_model import MediaContent
        m = MediaContent(media_type="image", media_path="/tmp/pic",
                         media_base64="QQ==", media_metadata={"format": "png"})
        urls = self._capture_urls(self._adapter(), [m])
        self.assertTrue(urls[0].startswith("data:image/png;base64,"), urls[0])

    def test_unknown_defaults_to_png(self):
        from llm_adapter_module.model.data_model import MediaContent
        m = MediaContent(media_type="image", media_path="/tmp/pic", media_base64="QQ==")
        urls = self._capture_urls(self._adapter(), [m])
        self.assertTrue(urls[0].startswith("data:image/png;base64,"), urls[0])


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
