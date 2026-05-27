# -*- coding: utf-8 -*-
"""
rag_module.extensions: 检索链路可选增强组件

包含:
    - BaseQueryRewriter / LLMQueryRewriter: 查询改写(文档第 12.2 节)
    - BaseReranker / LLMReranker: 检索结果重排(文档第 12.4 节)

这些是 RAG 检索链路的可选增强,通过 SimpleRAG 的 query_rewriter / reranker
参数注入,在 config 的 rag.enable_rewrite / rag.enable_rerank 开关下生效。

设计偏离声明:
    本子目录同 schema_module/deps_module/chunker_module,不采用 core/base.py +
    impl.py 二分(都是单文件 + 抽象/实现并存,因为每个组件本身小,二分反而冗余)。
"""

from .rewriter import BaseQueryRewriter, LLMQueryRewriter, RewriteResult
from .reranker import BaseReranker, LLMReranker

__all__ = [
    "BaseQueryRewriter",
    "LLMQueryRewriter",
    "RewriteResult",
    "BaseReranker",
    "LLMReranker",
]
