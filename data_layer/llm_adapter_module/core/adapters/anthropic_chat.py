# -*- coding: utf-8 -*-
"""
AnthropicChatAdapter — Claude 原生 API 适配器 (Task #35 引入, Task #45 加流式).

非 OpenAI 格式: x-api-key + anthropic-version 头, system 字段独立,
                max_tokens 必填; chat_stream 用 _post_stream_anthropic.

Task NN (#74): 从 impl.py 单文件拆出.
"""
from __future__ import annotations

import time
from typing import Dict, Any, List, Optional

from llm_adapter_module.core.base import BaseChatAdapter
from llm_adapter_module.model.data_model import LLMRequest, LLMResponse, LLMParam
from llm_adapter_module.utils.tool_functions import (
    gen_trace_id, now_ts,
    ensure_file_content_splits,
)

from ._http_mixin import _BaseHTTPAdapterMixin, _sanitize_api_base


class AnthropicChatAdapter(BaseChatAdapter, _BaseHTTPAdapterMixin):
    """Anthropic Claude 原生 API 适配器 (跟 OpenAI 格式不同)

    端点: POST {api_base}/v1/messages   默认 api_base=https://api.anthropic.com
    Header: x-api-key + anthropic-version (不是 Bearer)
    Payload: messages 数组 + system 单独字段 + max_tokens 必填
    Response: {content: [{type:"text", text:"..."}], stop_reason: ...}
    """

    def __init__(self, model_name, model_cfg, common_cfg, logger):
        self.model_name = model_name
        self.model_cfg = model_cfg
        self.common_cfg = common_cfg
        self.logger = logger
        self.api_key = str(model_cfg.get("api_key", "") or "")
        # Task XXXX-19 续: 跟 OpenAI 系 adapter 一样走 sanitizer, 拦 "undefined" 脏值
        self.api_base = _sanitize_api_base(
            model_cfg.get("api_base"), model_name=model_name, logger=logger,
            adapter_kind="anthropic",
        )
        self.anthropic_version = str(model_cfg.get("anthropic_version", "2023-06-01"))
        self.timeout = int(common_cfg.get("timeout", 30))
        self.max_retry = int(common_cfg.get("max_retry", 3))

    def check_config(self) -> bool:
        return bool(self.api_key and self.api_base)

    def generate(self, prompt: str, request: LLMRequest) -> str:
        return self.chat_with_context([{"role": "user", "content": prompt}], request)

    def chat_with_context(self, messages: List[Dict[str, Any]], request: LLMRequest) -> str:
        if not self.check_config():
            return f"[mock-anthropic:{self.model_name}] {messages[-1].get('content','')}"

        # Anthropic 把 system 拆出来, messages 只放 user/assistant
        system_text = ""
        api_messages: List[Dict[str, Any]] = []
        for m in messages:
            if m.get("role") == "system":
                system_text = (system_text + "\n" + str(m.get("content", ""))).strip()
            else:
                api_messages.append({"role": m["role"], "content": m.get("content", "")})

        url = f"{self.api_base}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
            "Content-Type": "application/json",
        }
        p: LLMParam = request.model_param
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": api_messages,
            "max_tokens": p.max_tokens or 1024,  # Anthropic max_tokens 必填
            "temperature": p.temperature,
        }
        if system_text:
            payload["system"] = system_text
        if p.extra_params:
            payload.update(p.extra_params)

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retry):
            try:
                data = self._post_json(url, headers, payload, timeout=self.timeout)
                # content: [{type: "text", text: "..."}]
                blocks = data.get("content", []) or []
                texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
                return "".join(texts)
            except Exception as e:
                last_err = e
                self.logger.warning(
                    f"[llm_adapter] Anthropic chat attempt {attempt+1} failed: {e}",
                    logger_name="llm_adapter",
                )
                time.sleep(min(2 ** attempt, 8))
        raise last_err or RuntimeError("anthropic chat failed")

    def call(self, request: LLMRequest) -> LLMResponse:
        start = now_ts()
        trace_id = gen_trace_id()
        try:
            if request.input_text:
                out = self.generate(request.input_text, request)
            elif request.file_content:
                fc = ensure_file_content_splits(request.file_content)
                prompt = "\n\n".join((fc.split_contents or [])[:5])
                out = self.generate(prompt, request)
            else:
                out = ""
            return LLMResponse(code="SUCCESS", message="ok", chat_result=out,
                              cost_time=now_ts() - start, trace_id=trace_id)
        except Exception as e:
            return LLMResponse(code="RAG_RUN_FAILED", message=str(e),
                              cost_time=now_ts() - start, trace_id=trace_id)

    def chat_stream(self, messages: List[Dict[str, Any]], request: LLMRequest):
        """Anthropic 真实 token 流式 (Task #45).

        Anthropic Messages API stream=true 后:
          - Header 同 chat_with_context (x-api-key + anthropic-version)
          - payload 加 "stream": true
          - SSE 事件 content_block_delta 含 delta.text 增量
        """
        if not self.check_config():
            # mock 降级: chat_with_context + 切片
            full = self.chat_with_context(messages, request)
            for i in range(0, len(full), 10):
                yield full[i:i + 10]
            return

        # Anthropic 把 system 拆出来
        system_text = ""
        api_messages: List[Dict[str, Any]] = []
        for m in messages:
            if m.get("role") == "system":
                system_text = (system_text + "\n" + str(m.get("content", ""))).strip()
            else:
                api_messages.append({"role": m["role"], "content": m.get("content", "")})

        url = f"{self.api_base}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        p: LLMParam = request.model_param
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": api_messages,
            "max_tokens": p.max_tokens or 1024,
            "temperature": p.temperature,
            "stream": True,
        }
        if system_text:
            payload["system"] = system_text
        if p.extra_params:
            payload.update(p.extra_params)

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retry):
            yielded = False
            try:
                for chunk in self._post_stream_anthropic(url, headers, payload, timeout=self.timeout):
                    yielded = True
                    yield chunk
                return
            except Exception as e:
                last_err = e
                # 已吐出 token 后不能重试: HTTP 流无法从中途续传, 重试会把前缀再吐一遍 → 输出重复错乱
                if yielded:
                    raise
                self.logger.warning(
                    f"[llm_adapter] Anthropic chat_stream attempt {attempt+1} failed: {e}",
                    logger_name="llm_adapter",
                )
                time.sleep(min(2 ** attempt, 8))
        if last_err:
            raise last_err
