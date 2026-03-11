# -*- coding: utf-8 -*-
"""
RAG 模块统一数据模型定义
遵循系统整体数据规范，使用 dataclass 定义请求/响应结构
"""

from typing import List, Optional, Dict
from dataclasses import dataclass


@dataclass
class RAGRequest:
    """RAG 增强生成统一请求模型"""
    # 用户问题/查询（必填）
    query: str
    # 返回最相似的片段数量（默认 5）
    top_k: int = 5
    # 生成大模型名称（默认使用系统配置）
    llm_model_name: Optional[str] = None
    # 向量模型名称（默认使用系统配置）
    embedding_model_name: Optional[str] = None
    # 上下文最大长度（默认使用系统配置）
    max_context_length: Optional[int] = None


@dataclass
class RetrievedChunk:
    """RAG 检索结果标准化模型"""
    vector_id: str
    score: float          # 相似度得分（0~1）
    doc_id: str           # 文档 ID（关联文档存储）
    chunk_id: str         # 文本片段 ID
    file_name: str        # 源文件名
    content: Optional[str] = None  # 片段文本内容


@dataclass
class RAGResponse:
    """RAG 增强生成统一响应模型，遵循系统统一异常码规范"""
    # 响应码：SUCCESS（成功）、系统异常码（失败）
    code: str
    # 响应信息：成功为"ok"，失败为具体错误信息
    message: str
    # 响应数据
    data: Optional[Dict] = None
    # 调用耗时（秒，可选）
    cost_time: Optional[float] = None
    # 链路追踪 ID（可选）
    trace_id: Optional[str] = None