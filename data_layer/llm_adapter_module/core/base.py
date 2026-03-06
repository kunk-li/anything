from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List, Dict, Tuple

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
