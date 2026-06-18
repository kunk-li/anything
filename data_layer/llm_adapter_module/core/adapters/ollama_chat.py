# -*- coding: utf-8 -*-
"""
OllamaChatAdapter — 本地 Ollama (qwen2.5 / llama3 等) 适配器 (Task #35).

无 API key (本机), endpoint POST {api_base}/api/chat,
chat_stream 用 NDJSON 而非 SSE.

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


class OllamaChatAdapter(BaseChatAdapter, _BaseHTTPAdapterMixin):
    """Ollama 本地大模型适配器 (无需 API key)

    端点: POST {api_base}/api/chat  默认 api_base=http://localhost:11434
    无需 Auth header (本机服务)
    Payload: {model, messages, stream:false, options:{temperature, num_predict}}
    Response: {message:{role,content}, done:true}

    用法: 先 `ollama pull qwen2.5:7b` 或 `ollama pull llama3.2:3b`,
          model_name 用对应 tag, api_base 默认即可。
    """

    def __init__(self, model_name, model_cfg, common_cfg, logger):
        self.model_name = model_name
        self.model_cfg = model_cfg
        self.common_cfg = common_cfg
        self.logger = logger
        # api_key 对 Ollama 是可选的 (反代加 token 时才用)
        self.api_key = str(model_cfg.get("api_key", "") or "")
        # Task XXXX-19 续: 走 sanitizer, 拦 "undefined" 脏值
        self.api_base = _sanitize_api_base(
            model_cfg.get("api_base"), model_name=model_name, logger=logger,
            adapter_kind="ollama",
        )
        self.timeout = int(common_cfg.get("timeout", 60))  # 本地推理可能稍慢
        self.max_retry = int(common_cfg.get("max_retry", 2))

    def check_config(self) -> bool:
        return bool(self.api_base)  # Ollama 不强制要 key

    def generate(self, prompt: str, request: LLMRequest) -> str:
        return self.chat_with_context([{"role": "user", "content": prompt}], request)

    def chat_with_context(self, messages: List[Dict[str, Any]], request: LLMRequest) -> str:
        if not self.check_config():
            return f"[mock-ollama:{self.model_name}] {messages[-1].get('content','')}"

        url = f"{self.api_base}/api/chat"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        p: LLMParam = request.model_param
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": m.get("role"), "content": m.get("content", "")} for m in messages],
            "stream": False,
            "options": {
                "temperature": p.temperature,
                "num_predict": p.max_tokens if p.max_tokens else -1,
            },
        }
        if p.extra_params:
            payload.update(p.extra_params)

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retry):
            try:
                data = self._post_json(url, headers, payload, timeout=self.timeout)
                msg = data.get("message", {}) or {}
                return str(msg.get("content", "") or "")
            except Exception as e:
                last_err = e
                self.logger.warning(
                    f"[llm_adapter] Ollama chat attempt {attempt+1} failed: {e}",
                    logger_name="llm_adapter",
                )
                time.sleep(min(2 ** attempt, 4))
        raise last_err or RuntimeError("ollama chat failed")

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
        """Ollama 真实 token 流式 (Task #45).

        Ollama /api/chat 用 NDJSON 而非 SSE, payload stream=true (默认行为)。
        """
        if not self.check_config():
            # mock 降级 (共用 _BaseHTTPAdapterMixin._mock_stream)
            yield from self._mock_stream(messages, request)
            return

        url = f"{self.api_base}/api/chat"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        p: LLMParam = request.model_param
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": m.get("role"), "content": m.get("content", "")} for m in messages],
            "stream": True,
            "options": {
                "temperature": p.temperature,
                "num_predict": p.max_tokens if p.max_tokens else -1,
            },
        }
        if p.extra_params:
            payload.update(p.extra_params)

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retry):
            yielded = False
            try:
                for chunk in self._post_stream_ollama(url, headers, payload, timeout=self.timeout):
                    yielded = True
                    yield chunk
                return
            except Exception as e:
                last_err = e
                # 已吐出 token 后不能重试: HTTP 流无法从中途续传, 重试会把前缀再吐一遍 → 输出重复错乱
                if yielded:
                    raise
                self.logger.warning(
                    f"[llm_adapter] Ollama chat_stream attempt {attempt+1} failed: {e}",
                    logger_name="llm_adapter",
                )
                time.sleep(min(2 ** attempt, 4))
        if last_err:
            raise last_err
