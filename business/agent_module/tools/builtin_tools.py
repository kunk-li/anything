# -*- coding: utf-8 -*-
"""
Agent 内置扩展工具集合 (Task #37 历史入口)

Task MM (#73): 工具实现已拆到 tools_impl/ 下每工具一个文件. 本文件留作
向后兼容的 re-export shim — 老 import `from agent_module.tools.builtin_tools
import calculator_tool` 仍可用. 加新工具直接进 tools_impl/<name>.py.

跟前 1655 行单文件版的差异:
    - calculator_tool 等公共 API 一字不改
    - 每工具一个文件: 加新工具/改老工具不再滚到 1500 行下面找位置
    - TOOL_DESCRIPTIONS 独立到 tools_impl/_descriptions.py
"""

from .tools_impl import (
    calculator_tool,
    datetime_tool,
    wikipedia_tool,
    make_document_read_tool,
    regex_extract,
    text_stats,
    json_query,
    http_get,
    make_text_summarize_tool,
    code_lint,
    make_email_send_tool,
    make_image_describe_tool,
    weather,
    currency_convert,
    python_sandbox,
    make_spawn_subagent_tool,
    web_search,                   # Task HHH (#94)
    image_generate_tool,          # Task TTTT-6 (#143)
    pdf_read,                     # Task TTTT-4 (#141)
    excel_read,                   # Task TTTT-4 (#141)
    sql_query,                    # Task TTTT-3 (#140)
    TOOL_DESCRIPTIONS,
)

__all__ = [
    "calculator_tool",
    "datetime_tool",
    "wikipedia_tool",
    "make_document_read_tool",
    "regex_extract",
    "text_stats",
    "json_query",
    "http_get",
    "make_text_summarize_tool",
    "code_lint",
    "make_email_send_tool",
    "make_image_describe_tool",
    "weather",
    "currency_convert",
    "python_sandbox",
    "make_spawn_subagent_tool",
    "web_search",                  # Task HHH (#94)
    "image_generate_tool",         # Task TTTT-6 (#143)
    "pdf_read",                    # Task TTTT-4 (#141)
    "excel_read",                  # Task TTTT-4 (#141)
    "sql_query",                   # Task TTTT-3 (#140)
    "TOOL_DESCRIPTIONS",
]
