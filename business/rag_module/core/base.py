# -*- coding: utf-8 -*-
"""
RAG 模块抽象基类

签名严格对齐 SimpleRAG 实际实现，请求统一采用 dict 风格（system 文档第 8 章定义
的统一请求格式），避免抽象/实现签名漂移导致上游做"签名探测"。
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class BaseRAG(ABC):
    """RAG 模块抽象基类，所有 RAG 实现类必须继承此类"""

    @abstractmethod
    def retrieve(self, request: Dict[str, Any]) -> List[Dict[str, Any]]:
        """执行 chunk 级检索并返回标准化结果列表。

        参数:
            request: 统一请求 dict，至少包含 query；可选 top_k / trace_id / extra_params.filters

        返回:
            chunk 级结果列表，每项至少包含 chunk_id/doc_id/file_name/score/content。
        """

    @abstractmethod
    def generate(self, request: Dict[str, Any], retrieved_chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """基于 chunk 级检索结果生成回答与 citations。

        参数:
            request: 统一请求 dict（同 retrieve）
            retrieved_chunks: retrieve 的返回结果

        返回:
            {"answer": str, "citations": List[Dict]}
        """

    @abstractmethod
    def run(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """执行完整 RAG 流程（对外核心入口）。

        参数:
            request: 统一请求 dict

        返回:
            统一响应信封:
            {code, message, data: {answer, citations, retrieved_chunks}, trace_id, retryable, details, cost_time}
        """

    @abstractmethod
    def call_rag(
        self,
        query: str,
        top_k: int = 5,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """关键字参数风格的便捷入口（等价于 run，调用方无需手工构造 request dict）。"""
