from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List, Dict, Tuple, Optional

from llm_adapter_module.model.data_model import LLMRequest, LLMResponse, MediaContent, MultimodalResult, FileContent


class BaseLLMAdapter(ABC):
    """大模型通用适配器抽象基类"""

    @abstractmethod
    def __init__(self, model_name: str):
        pass

    @abstractmethod
    def call(self, request: LLMRequest) -> LLMResponse:
        pass

    @abstractmethod
    def check_config(self) -> bool:
        pass


class BaseVectorAdapter(BaseLLMAdapter):
    """向量模型适配器抽象基类"""

    @abstractmethod
    def embed_single(self, text: str, request: LLMRequest) -> List[float]:
        pass

    @abstractmethod
    def embed_batch(self, texts: List[str], request: LLMRequest) -> List[List[float]]:
        pass


class BaseChatAdapter(BaseLLMAdapter):
    """聊天模型适配器抽象基类"""

    @abstractmethod
    def generate(self, prompt: str, request: LLMRequest) -> str:
        pass

    @abstractmethod
    def chat_with_context(self, messages: List[Dict[str, Any]], request: LLMRequest) -> str:
        pass


class BaseMultimodalAdapter(BaseLLMAdapter):
    """多模态模型适配器抽象基类"""

    @abstractmethod
    def understand_text_media(self, text: str, media_list: List[MediaContent], request: LLMRequest) -> MultimodalResult:
        pass

    @abstractmethod
    def media_to_text(self, media_list: List[MediaContent], request: LLMRequest) -> str:
        pass

    @abstractmethod
    def multimodal_chat(self, messages: List[Dict[str, Any]], request: LLMRequest) -> MultimodalResult:
        pass


class BaseLLMService(ABC):
    """大模型统一服务抽象基类"""

    @abstractmethod
    def init_adapters(self) -> None:
        pass

    @abstractmethod
    def call_llm(self, request: LLMRequest) -> LLMResponse:
        pass

    @abstractmethod
    def call_by_file(self, file_content: FileContent, request_type: str, model_param: Any = None) -> LLMResponse:
        pass

    @abstractmethod
    def validate_request(self, request: LLMRequest) -> Tuple[bool, str]:
        pass

    @abstractmethod
    def generate(self, prompt: str, trace_id: Optional[str] = None) -> str:
        """统一文本生成入口（强制签名，所有实现必须严格匹配）。

        作为 LLMRequest/LLMResponse 之外的简化对接点，便于上层 RAG/Agent
        无需构造完整 LLMRequest 即可拿到纯文本回答。

        参数：
            prompt: 用户输入的提示词
            trace_id: 可选追踪 ID，便于全链路日志检索

        返回：
            模型生成的纯文本回答（非空字符串）

        异常：
            上游调用失败应抛出明确异常（如 RuntimeError），
            禁止静默返回空串或错误占位文本，由上层决定如何回退。
        """
