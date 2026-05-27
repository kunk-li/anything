# -*- coding: utf-8 -*-
"""
schema_module: 系统统一数据 Schema（基于 Pydantic v2）

提供：
    - RequestEnvelope: 系统统一请求格式（文档第 8 章）
    - ResponseEnvelope: 系统统一响应信封（文档第 10 章）

设计偏离声明：
    本模块不采用 core/base.py + impl.py 二分结构，因为 schema 是声明式数据契约，
    不存在"抽象接口/具体实现"二分关系。详见 README.md。
"""

from .schema import (
    RequestEnvelope,
    ResponseEnvelope,
    RequestType,
    validate_request_dict,
    # 内部数据 schema
    Chunk,
    ChunkMeta,
    RetrievedChunk,
    Citation,
    ToolStep,
)

__all__ = [
    "RequestEnvelope",
    "ResponseEnvelope",
    "RequestType",
    "validate_request_dict",
    "Chunk",
    "ChunkMeta",
    "RetrievedChunk",
    "Citation",
    "ToolStep",
]
