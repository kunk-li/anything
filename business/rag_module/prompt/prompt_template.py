# -*- coding: utf-8 -*-
"""
RAG 增强生成 Prompt 模板
支持配置化切换模板，此处为默认模板
"""

# 默认 RAG Prompt 模板
RAG_PROMPT_TEMPLATE = """
请根据以下资料回答问题。如果资料中没有相关信息，请直接回答“根据现有资料无法回答”。

资料内容：
{context}

问题：{query}
答案：
"""