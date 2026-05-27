# -*- coding: utf-8 -*-
"""
chunker_module: 索引构建阶段的文本切分工具

依据文档第 11 章 Chunking 规范实现,对外暴露最常用的两个入口:
    - chunk_document: 把整篇文档切成符合规范的 chunk 列表
    - build_upsert_items: 把 chunks + vectors 组合成 vector_db 可吞下的 upsert items

设计偏离声明:
    本模块同 schema_module/deps_module,不采用 core/base.py + impl.py 二分 —
    chunker 是无状态算法函数集,没有"抽象接口/具体实现"二分需求。
    若未来要支持多种切分策略(代码/表格特化),可在本模块内增加 strategies/ 子目录
    或新建 BaseChunker ABC,届时再演进。
"""

from .chunker import (
    estimate_tokens,
    normalize_text,
    split_by_natural_boundaries,
    chunk_document,
    build_upsert_items,
)

__all__ = [
    "estimate_tokens",
    "normalize_text",
    "split_by_natural_boundaries",
    "chunk_document",
    "build_upsert_items",
]
