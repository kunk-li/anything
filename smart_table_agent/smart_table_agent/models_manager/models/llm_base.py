# llm_base.py
from abc import ABC, abstractmethod


class LLMBase(ABC):
    """
    所有语言大模型的通用抽象基类
    子类只需要实现：_request(self, messages) -> str
    """

    def __init__(self, model_name: str = None, api_key: str = None):
        self._model_name = model_name
        self._api_key = api_key
        self.chat_history = []  # 可选：保存对话上下文

    def reset(self):
        """清空对话上下文"""
        self.chat_history = []

    @abstractmethod
    def single_request(self, messages):
        """
        抽象方法，子类必须重写，负责实际发送 单次HTTP 请求到模型
        """
        raise NotImplementedError("子类必须实现 single_request 方法")

    @abstractmethod
    def multiple_requests(self):
        """
        抽象方法，子类必须重写，负责实际发送 多轮对话的HTTP 请求到模型
        """
        raise NotImplementedError("子类必须实现 multiple_requests 方法")
