# -*- coding: utf-8 -*-
"""
RAG 模块抽象基类
定义模块核心接口，强制所有实现类必须实现，保障模块一致性、可替换性
"""

from abc import ABC, abstractmethod
from typing import List, Dict

from ..model.data_model import RAGRequest, RAGResponse


class BaseRAG(ABC):
    """RAG 模块抽象基类，所有 RAG 实现类必须继承此类"""

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        检索步骤：根据用户问题检索相关文本片段
        :param query: 用户问题
        :param top_k: 返回片段数量
        :return: 标准化检索结果列表
        :raises RAGException: 检索失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def generate(self, query: str, contexts: List[str]) -> str:
        """
        生成步骤：根据检索上下文生成增强回答
        :param query: 用户问题
        :param contexts: 检索到的文本片段列表
        :return: 生成的回答文本
        :raises RAGException: 生成失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def run(self, query: str, top_k: int = 5) -> Dict:
        """
        RAG 全流程执行入口（对外核心接口）
        :param query: 用户问题
        :param top_k: 返回片段数量
        :return: 标准化 RAG 响应结果 (Dict 格式，兼容 RAGResponse.data)
        :raises RAGException: 流程执行失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def call_rag(self, request: RAGRequest) -> RAGResponse:
        """
        统一 RAG 调用接口（对外标准化入口）
        :param request: RAG 请求模型
        :return: RAG 响应模型
        """
        pass