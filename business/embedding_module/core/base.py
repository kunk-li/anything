from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ..model.data_model import EmbeddingRequest, EmbeddingResponse


class BaseEmbedding(ABC):
    """Embedding 模块抽象基类，统一定义核心接口。"""

    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """
        单条文本向量化
        :param text: 输入文本
        :return: 一维浮点型向量列表
        """
        raise NotImplementedError

    @abstractmethod
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        批量文本向量化
        :param texts: 输入文本列表
        :return: 二维浮点型向量列表
        """
        raise NotImplementedError

    @abstractmethod
    def call_embedding(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """
        统一向量化调用入口
        :param request: 统一请求模型
        :return: 统一响应模型
        """
        raise NotImplementedError