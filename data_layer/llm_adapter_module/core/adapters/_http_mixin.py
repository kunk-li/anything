# -*- coding: utf-8 -*-
"""
_BaseHTTPAdapterMixin — 给需要 HTTP 调用的适配器提供通用 requests 能力
                       (POST JSON + 三家 SSE / NDJSON 流式 generator)

Task NN (#74): 从 impl.py 1261 行单文件拆出, 让 5 个适配器各自单文件依赖此 mixin.
"""
from __future__ import annotations

from typing import Dict, Any

import requests


class _BaseHTTPAdapterMixin:
    """给需要HTTP调用的适配器提供通用requests能力（超时、重试 + SSE 流式）"""

    def _post_json(self, url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def _post_stream_openai(
        self, url: str, headers: Dict[str, str],
        payload: Dict[str, Any], timeout: int,
    ):
        """OpenAI 兼容 SSE 流式 generator. yield 每个 delta.content (str).

        SSE 协议每行 'data: {json}' 或 'data: [DONE]', 解析 choices[0].delta.content。
        DashScope / DeepSeek / Moonshot / 其他 OpenAI 兼容 endpoint 都走此格式。
        """
        import json as _json
        resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=timeout)
        resp.raise_for_status()
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if not data_str or data_str == "[DONE]":
                continue
            try:
                obj = _json.loads(data_str)
            except Exception:
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            # 优先 delta.content (流式 chunk), 兜底 message.content
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                yield content

    def _post_stream_anthropic(
        self, url: str, headers: Dict[str, str],
        payload: Dict[str, Any], timeout: int,
    ):
        """Anthropic Messages API SSE 流式 generator.

        Anthropic event format 跟 OpenAI 不一样, 每个事件块多行:
            event: content_block_delta
            data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}

        我们只关心 content_block_delta 事件里 delta.text_delta, 其他 (message_start /
        content_block_stop / message_stop) 忽略。
        """
        import json as _json
        resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=timeout)
        resp.raise_for_status()
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if not data_str:
                continue
            try:
                obj = _json.loads(data_str)
            except Exception:
                continue
            if obj.get("type") != "content_block_delta":
                continue
            delta = obj.get("delta") or {}
            if delta.get("type") in ("text_delta", "text"):
                text = delta.get("text")
                if text:
                    yield text

    def _post_stream_ollama(
        self, url: str, headers: Dict[str, str],
        payload: Dict[str, Any], timeout: int,
    ):
        """Ollama /api/chat 流式 generator. Ollama 用 NDJSON 而非 SSE.

        每行一个完整 JSON:
            {"model":"llama3","message":{"content":"Hi"},"done":false}
            {"model":"llama3","message":{"content":""},"done":true,"total_duration":...}

        yield 每个 message.content, 直到 done=true。
        """
        import json as _json
        resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=timeout)
        resp.raise_for_status()
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            try:
                obj = _json.loads(line)
            except Exception:
                continue
            msg = obj.get("message") or {}
            content = msg.get("content")
            if content:
                yield content
            if obj.get("done"):
                return
