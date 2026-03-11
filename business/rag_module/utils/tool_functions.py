# -*- coding: utf-8 -*-
"""
RAG 模块专属工具函数
包含上下文拼接、Prompt 渲染辅助、检索结果处理等
"""

from typing import List


def assemble_contexts(contexts: List[str]) -> str:
    """
    上下文拼接工具
    :param contexts: 检索到的文本片段列表
    :return: 拼接后的完整上下文字符串
    """
    if not contexts:
        return ""

    # 按片段序号拼接
    context_text = "\n\n".join([f"[片段 {i + 1}]\n{c}" for i, c in enumerate(contexts)])
    return context_text


def truncate_context(context: str, max_length: int) -> str:
    """
    上下文截断工具
    :param context: 原始文本
    :param max_length: 最大长度
    :return: 截断后的文本
    """
    if len(context) <= max_length:
        return context
    return context[:max_length] + "..."