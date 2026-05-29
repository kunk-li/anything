# -*- coding: utf-8 -*-
"""
tools_impl/ — 每工具一个文件 (Task MM #73)

builtin_tools.py 原 1655 行的"15+1 工具大合集"按工具拆到本目录, 每个文件
专门负责一个工具的实现 + 私有辅助函数. 添加新工具不再需要找位置.

文件命名跟工具名一致 (snake_case):
    calculator.py       — calculator_tool + _safe_eval
    datetime_tool.py    — datetime_tool
    wikipedia.py        — wikipedia_tool
    document_read.py    — make_document_read_tool 工厂
    regex_extract.py    — regex_extract
    text_stats.py       — text_stats
    json_query.py       — json_query + _json_query_walk 助手
    http_get.py         — http_get + _is_private_ip / _resolve_safe SSRF 防御
    text_summarize.py   — make_text_summarize_tool 工厂
    code_lint.py        — code_lint
    email_send.py       — make_email_send_tool 工厂
    image_describe.py   — make_image_describe_tool 工厂
    weather.py          — weather (Open-Meteo)
    currency_convert.py — currency_convert (Frankfurter)
    python_sandbox.py   — python_sandbox + _sandbox_validate_ast
    spawn_subagent.py   — make_spawn_subagent_tool + _RestrictedRegistry (Task EE)
    _descriptions.py    — TOOL_DESCRIPTIONS dict

后向兼容: 老 import `from agent_module.tools.builtin_tools import calculator_tool`
仍可用 (见 builtin_tools.py 重新 export).
"""

from .calculator import calculator_tool
from .datetime_tool import datetime_tool
from .wikipedia import wikipedia_tool
from .document_read import make_document_read_tool
from .regex_extract import regex_extract
from .text_stats import text_stats
from .json_query import json_query
from .http_get import http_get
from .text_summarize import make_text_summarize_tool
from .code_lint import code_lint
from .email_send import make_email_send_tool
from .image_describe import make_image_describe_tool
from .weather import weather
from .currency_convert import currency_convert
from .python_sandbox import python_sandbox
from .spawn_subagent import make_spawn_subagent_tool
from .web_search import web_search   # Task HHH (#94)
from .image_generate import image_generate_tool  # Task TTTT-6 (#143)
from .pdf_excel_read import pdf_read, excel_read  # Task TTTT-4 (#141)
from .sql_query import sql_query  # Task TTTT-3 (#140)
from ._descriptions import TOOL_DESCRIPTIONS

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
    "web_search",                   # Task HHH (#94)
    "image_generate_tool",          # Task TTTT-6 (#143)
    "pdf_read",                     # Task TTTT-4 (#141)
    "excel_read",                   # Task TTTT-4 (#141)
    "sql_query",                    # Task TTTT-3 (#140)
    "TOOL_DESCRIPTIONS",
]
