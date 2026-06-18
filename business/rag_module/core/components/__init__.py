# -*- coding: utf-8 -*-
"""
SimpleRAG 组件 mixins (god-file 拆分)

把原 SimpleRAG god class 里几组内聚方法机械搬到 mixin (零行为变更, self.x() 解析不变):

    RagSessionMemoryMixin — 会话历史读写 + Phase4 记忆个性化 (画像注入 / 跨会话学习)
    RagRetrievalMixin     — query 规范化/向量化/向量库+BM25 检索/RRF 融合/取文/rerank
    RagGenerationMixin    — 上下文拼装 / prompt 构建 / LLM 生成 / citations

设计同 agent_module: 全用 mixin 而非 composition, 保持 self.xxx 调用风格, 测试 0 改动。
公开抽象方法 (retrieve/generate/run/call_rag) 留在 impl.py —— 它们是 BaseRAG 的
@abstractmethod 实现, 必须直接挂在 SimpleRAG 上 (mixin 排在 BaseRAG 之后会被抽象版先解析)。
"""

from .rag_session_memory import RagSessionMemoryMixin
from .rag_retrieval import RagRetrievalMixin
from .rag_generation import RagGenerationMixin

__all__ = [
    "RagSessionMemoryMixin",
    "RagRetrievalMixin",
    "RagGenerationMixin",
]
