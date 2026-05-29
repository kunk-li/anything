# -*- coding: utf-8 -*-
"""
Tools 目录: ToolRegistry (本身) + builtin_tools (扩展工具集)
"""

from .builtin_tools import (
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
    make_spawn_subagent_tool,  # Task EE (#65)
    web_search,                # Task HHH (#94)
    image_generate_tool,       # Task TTTT-6 (#143)
    pdf_read,                  # Task TTTT-4 (#141)
    excel_read,                # Task TTTT-4 (#141)
    sql_query,                 # Task TTTT-3 (#140)
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
    "web_search",
    "image_generate_tool",
    "pdf_read",
    "excel_read",
    "sql_query",
    "TOOL_DESCRIPTIONS",
]