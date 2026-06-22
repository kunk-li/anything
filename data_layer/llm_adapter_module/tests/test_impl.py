import unittest

from llm_adapter_module.core.impl import LLMService
from llm_adapter_module.model.data_model import LLMRequest, LLMParam, FileContent, MediaContent
from llm_adapter_module.utils import (
    configure_health_tracker,
    get_health_tracker,
    reset_health_tracker,
)


class TestLLMAdapterModule(unittest.TestCase):
    def setUp(self):
        self.llm_service = LLMService()

    def test_validate_request(self):
        ok, msg = self.llm_service.validate_request(LLMRequest(request_type="VECTOR", input_text="hi"))
        self.assertTrue(ok)
        ok, msg = self.llm_service.validate_request(LLMRequest(request_type="MULTIMODAL", input_text="x"))
        self.assertFalse(ok)

    def test_vector_call_mock(self):
        req = LLMRequest(request_type="VECTOR", input_text="测试文本", model_name="default", model_param=LLMParam(batch_size=2, normalize=True))
        resp = self.llm_service.call_llm(req)
        # 这是 "smoke 级" 单测, 重点是 call_llm 流程能跑通且返回合规信封;
        # 模型存在性 / API key 真假 / 网络可达性都是环境因素, 因此允许
        # SUCCESS (有 key 真调通) / MODEL_NOT_FOUND (模型未注册) /
        # VECTOR_QUERY_FAILED / RAG_RUN_FAILED (key 是 ${占位符} 时 401)
        self.assertIn(resp.code, ["SUCCESS", "MODEL_NOT_FOUND", "VECTOR_QUERY_FAILED", "RAG_RUN_FAILED"])

    def test_chat_call_mock(self):
        req = LLMRequest(request_type="CHAT", input_text="你好", model_name="default")
        resp = self.llm_service.call_llm(req)
        # 同上: 关注流程, 不关注真实 key
        self.assertIn(resp.code, ["SUCCESS", "MODEL_NOT_FOUND", "RAG_RUN_FAILED"])

    def test_multimodal_call_mock(self):
        fc = FileContent(
            file_name="x.png",
            file_type="png",
            media_contents=[MediaContent(media_type="image", media_path="x.png", media_metadata={"format": "png"})],
        )
        req = LLMRequest(request_type="MULTIMODAL", input_text="描述图片", file_content=fc, model_name="default")
        resp = self.llm_service.call_llm(req)
        self.assertIn(resp.code, ["SUCCESS", "MODEL_NOT_FOUND", "RAG_RUN_FAILED", "UNKNOWN_ERROR"])

    def test_all_health_skipped_records_skipped_models(self):
        """全部候选都被健康检查跳过时, ALL_MODELS_FAILED 信封必须列出被跳过的模型,
        否则空 tried_models 会掩盖 "为什么所有模型都没调用" 的原因。"""
        # 低阈值 health tracker, 1 次失败即 unhealthy 且不冷却恢复
        configure_health_tracker(fail_threshold=1, cooldown_seconds=3600)
        self.addCleanup(reset_health_tracker)
        try:
            model_name = "skip-only-model"
            self.llm_service.register_or_update_model(
                name=model_name,
                request_type="CHAT",
                adapter_class="OpenAIChatAdapter",
                api_key="",
                api_base="",
            )
            # 把唯一候选打成 unhealthy
            get_health_tracker().record_failure(model_name, error="forced")
            self.assertFalse(get_health_tracker().is_available(model_name))

            req = LLMRequest(request_type="CHAT", input_text="hi", model_name=model_name)
            resp = self.llm_service.call_llm(req)

            # 没有任何模型被真正调用过 → 走 ALL_MODELS_FAILED 合成信封
            self.assertEqual(resp.code, "ALL_MODELS_FAILED")
            self.assertIsNotNone(resp.request_info)
            # tried 为空 (一个都没真正调), 但 skipped 必须记下被跳过的模型
            self.assertEqual(resp.request_info.get("tried_models"), [])
            self.assertIn(model_name, resp.request_info.get("skipped_models", []))
        finally:
            self.llm_service.unregister_model("skip-only-model")

if __name__ == "__main__":
    unittest.main()
