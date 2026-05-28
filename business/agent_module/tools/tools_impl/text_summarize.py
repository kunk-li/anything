# -*- coding: utf-8 -*-
"""
Tool: text_summarize (Task MM #73)
"""

from __future__ import annotations

import ast
import ipaddress
import json
import math
import operator
import re
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

# ============================================================
# 9. text_summarize — LLM 压缩 (工厂模式闭包 llm_call)
# ============================================================

def make_text_summarize_tool(
    llm_call: Callable[[str], str],
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """工厂: 闭包 llm_call (str -> str) 返回 summarize 工具.

    payload:
        text: 待压缩文本 (必填)
        max_sentences: 目标句数 (默认 3, 上限 10)
        lang: "zh"|"en" (影响 prompt 模板, 默认 zh)

    返回 data: {"summary", "original_length", "summary_length"}
    """
    def _summarize(payload: Dict[str, Any]) -> Dict[str, Any]:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return {"code": "PARAM_MISSING", "message": "text 不能为空", "data": None, "retryable": False}
        if len(text) > 50_000:
            text = text[:50_000]  # 防 LLM context 爆掉
        max_sentences = max(1, min(int(payload.get("max_sentences", 3) or 3), 10))
        lang = str(payload.get("lang", "zh")).lower()

        if lang == "en":
            prompt = (
                f"Summarize the following text into {max_sentences} concise sentences. "
                "Preserve key facts and proper nouns. Reply with summary only, no preamble.\n\n"
                f"---\n{text}\n---"
            )
        else:
            prompt = (
                f"请把以下文本压缩成 {max_sentences} 句话, 保留关键事实和专有名词。"
                "只输出压缩后的内容, 不要任何解释或前言。\n\n"
                f"---\n{text}\n---"
            )

        try:
            summary = str(llm_call(prompt) or "").strip()
        except Exception as e:
            return {"code": "TOOL_CALL_FAILED", "message": f"LLM 调用失败: {e}",
                    "data": None, "retryable": True}

        return {
            "code": "SUCCESS", "message": "ok",
            "data": {
                "summary": summary,
                "original_length": len(text),
                "summary_length": len(summary),
                "max_sentences": max_sentences,
                "lang": lang,
            },
            "retryable": False,
        }
    return _summarize


