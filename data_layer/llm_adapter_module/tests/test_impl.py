import unittest
from unittest.mock import patch

from llm_adapter_module.core.impl import LLMService
from llm_adapter_module.model.data_model import LLMRequest, LLMParam, FileContent, MediaContent

class FakeResp:
    def __init__(self, json_data, status=200):
        self._json = json_data
        self.status_code = status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception("http error")
    def json(self):
        return self._json

class TestLLMAdapterModule(unittest.TestCase):
    def setUp(self):
        # 为了让测试可运行，这里 patch 配置读取，模拟存在一个 openai 模型
        self.patcher_cfg = patch("llm_adapter_module.config.config.ConfigManager")
        MockCM = self.patcher_cfg.start()
        cm = MockCM.return_value
        cm.load_config.return_value = None
        cm.get_config.side_effect = lambda key, default=None: {
            "llm.": {
                "default_vector_model": "text-embedding-ada-002",
                "default_chat_model": "gpt-3.5-turbo",
                "default_multimodal_model": "gpt-4-vision-preview",
                "openai": {
                    "text-embedding-ada-002": {
                        "api_key": "test",
                        "api_base": "https://api.openai.com/v1",
                        "request_type": "VECTOR",
                        "adapter_class": "OpenAIVectorAdapter",
                    },
                    "gpt-3.5-turbo": {
                        "api_key": "test",
                        "api_base": "https://api.openai.com/v1",
                        "request_type": "CHAT",
                        "adapter_class": "OpenAIChatAdapter",
                    },
                    "gpt-4-vision-preview": {
                        "api_key": "test",
                        "api_base": "https://api.openai.com/v1",
                        "request_type": "MULTIMODAL",
                        "adapter_class": "OpenAIMultimodalAdapter",
                        "support_media": ["image"],
                        "max_media_size": 20,
                    },
                },
                "common": {"timeout": 30, "max_retry": 1}
            }
        }.get(key, default)

        self.service = LLMService()

    def tearDown(self):
        self.patcher_cfg.stop()

    @patch("requests.post")
    def test_vector_call(self, mpost):
        mpost.return_value = FakeResp({
            "data": [{"embedding": [1.0, 0.0, 0.0]}]
        })
        req = LLMRequest(request_type="VECTOR", input_text="hello", model_name="text-embedding-ada-002", model_param=LLMParam(normalize=False))
        resp = self.service.call_llm(req)
        self.assertEqual(resp.code, "SUCCESS")
        self.assertEqual(resp.vector_result[0], [1.0, 0.0, 0.0])

    @patch("requests.post")
    def test_chat_call(self, mpost):
        mpost.return_value = FakeResp({
            "choices": [{"message": {"content": "hi"}}]
        })
        req = LLMRequest(request_type="CHAT", input_text="hello", model_name="gpt-3.5-turbo")
        resp = self.service.call_llm(req)
        self.assertEqual(resp.code, "SUCCESS")
        self.assertEqual(resp.chat_result, "hi")

    @patch("requests.post")
    def test_multimodal_call(self, mpost):
        mpost.return_value = FakeResp({
            "choices": [{"message": {"content": "image ok"}}]
        })
        # media_path 不读取文件（避免IO），直接提供 base64
        media = MediaContent(media_type="image", media_path="test.png", media_base64="aGVsbG8=")
        req = LLMRequest(request_type="MULTIMODAL", input_text="what is this", media_input=[media], model_name="gpt-4-vision-preview")
        resp = self.service.call_llm(req)
        self.assertEqual(resp.code, "SUCCESS")
        self.assertEqual(resp.multimodal_result.text_result, "image ok")

    def test_validate_invalid(self):
        req = LLMRequest(request_type="MULTIMODAL", input_text="no media", model_name="gpt-4-vision-preview")
        ok, msg = self.service.validate_request(req)
        self.assertFalse(ok)
        self.assertIn("多模态请求需提供媒体输入", msg)

if __name__ == "__main__":
    unittest.main()
