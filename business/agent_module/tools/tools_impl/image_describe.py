# -*- coding: utf-8 -*-
"""
Tool: image_describe (Task MM #73)
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
# 12. image_describe — 多模态 LLM 工厂
# ============================================================

def make_image_describe_tool(
    llm_service: Any,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """工厂: 闭包 llm_service (LLMService 实例) 返回图片理解工具.

    payload:
        image_path: 本地路径 (二选一)
        image_base64: base64 字符串 (二选一)
        prompt: 提问文本, 默认 "请描述这张图片"
        model_name: 可选, 走 LLMService.cfg 默认多模态模型

    返回 data: {"description", "model_name", "image_source"}

    依赖: llm_service 必须有 call_llm 方法 (LLMService 而非 DummyLLMClient)。
    """
    def _describe(payload: Dict[str, Any]) -> Dict[str, Any]:
        if llm_service is None or not hasattr(llm_service, "call_llm"):
            return {
                "code": "SERVICE_UNAVAILABLE",
                "message": "llm_service 未注入或不支持 call_llm",
                "data": None, "retryable": False,
            }

        image_path = payload.get("image_path")
        image_base64 = payload.get("image_base64")
        if not image_path and not image_base64:
            return {
                "code": "PARAM_MISSING",
                "message": "image_path 或 image_base64 二选一必填",
                "data": None, "retryable": False,
            }

        prompt = str(payload.get("prompt") or "请描述这张图片")
        model_name = str(payload.get("model_name") or "default")

        # 构造 MediaContent
        try:
            from llm_adapter_module.model.data_model import (
                LLMRequest, LLMParam, MediaContent,
            )
        except Exception as e:
            return {
                "code": "SERVICE_UNAVAILABLE",
                "message": f"llm_adapter 模块不可用: {e}",
                "data": None, "retryable": False,
            }

        media = MediaContent(
            media_type="image",
            media_path=str(image_path or ""),
            media_base64=str(image_base64 or "") or None,
        )

        req = LLMRequest(
            request_type="MULTIMODAL",
            input_text=prompt,
            media_input=[media],
            model_name=model_name,
            model_param=LLMParam(temperature=0.3, max_tokens=512),
        )

        try:
            resp = llm_service.call_llm(req)
        except Exception as e:
            return {
                "code": "TOOL_CALL_FAILED",
                "message": f"多模态 LLM 调用异常: {e}",
                "data": None, "retryable": True,
            }

        code = getattr(resp, "code", "UNKNOWN_ERROR")
        if code != "SUCCESS":
            return {
                "code": "TOOL_CALL_FAILED",
                "message": f"多模态 LLM 返回 {code}: {getattr(resp, 'message', '')}",
                "data": None, "retryable": True,
            }

        result = getattr(resp, "multimodal_result", None)
        text = getattr(result, "text_result", "") if result else ""
        return {
            "code": "SUCCESS", "message": "ok",
            "data": {
                "description": text,
                "model_name": getattr(resp, "request_info", {}).get("model_name") if getattr(resp, "request_info", None) else model_name,
                "image_source": "path" if image_path else "base64",
            },
            "retryable": False,
        }

    return _describe


