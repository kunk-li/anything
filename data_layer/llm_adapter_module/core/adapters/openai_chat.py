# -*- coding: utf-8 -*-
"""
OpenAIChatAdapter — OpenAI / OpenAI 兼容 (DashScope / DeepSeek / Moonshot / ...) chat 适配器.

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


class OpenAIChatAdapter(BaseChatAdapter, _BaseHTTPAdapterMixin):
    """OpenAI 聊天模型适配器（HTTP示例实现，支持单轮与多轮）"""

    def __init__(self, model_name: str, model_cfg: Dict[str, Any], common_cfg: Dict[str, Any], logger: "SystemLogger"):
        self.model_name = model_name
        self.model_cfg = model_cfg
        self.common_cfg = common_cfg
        self.logger = logger
        self.api_key = str(model_cfg.get("api_key", "") or "")
        # Task XXXX-19: api_base 走 _sanitize_api_base 兜底, 不再让 "undefined"
        # 这种脏值变成 https://undefined/... (会导致 host=undefined 神秘超时)
        self.api_base = _sanitize_api_base(
            model_cfg.get("api_base"), model_name=model_name, logger=logger,
        )
        self.timeout = int(common_cfg.get("timeout", 30))
        self.max_retry = int(common_cfg.get("max_retry", 3))

    def check_config(self) -> bool:
        return bool(self.api_key and self.api_base)

    def generate(self, prompt: str, request: LLMRequest) -> str:
        messages = [{"role": "user", "content": prompt}]
        return self.chat_with_context(messages, request)

    def chat_with_context(self, messages: List[Dict[str, Any]], request: LLMRequest) -> str:
        if not self.check_config():
            # 测试降级：返回固定回复
            return f"[mock-chat:{self.model_name}] {messages[-1].get('content','')}"

        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        p: LLMParam = request.model_param
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": p.temperature,
            "max_tokens": p.max_tokens,
        }
        if p.extra_params:
            payload.update(p.extra_params)

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retry):
            try:
                data = self._post_json(url, headers, payload, timeout=self.timeout)
                # ZZ-8: 提取真实 token usage (OpenAI/DashScope/DeepSeek 响应都含 usage 字段),
                # 存实例供 call() 填进 LLMResponse — 不再只靠 字符数//4 估算.
                self._last_usage = data.get("usage") or {}
                choices = data.get("choices", [])
                if not choices:
                    return ""
                msg = choices[0].get("message", {})
                return msg.get("content", "") or ""
            except Exception as e:
                last_err = e
                self.logger.warning(f"[llm_adapter] OpenAI chat attempt {attempt+1} failed: {e}", logger_name="llm_adapter")
                time.sleep(min(2 ** attempt, 8))
        raise last_err or RuntimeError("chat failed")

    def call(self, request: LLMRequest) -> LLMResponse:
        start = now_ts()
        trace_id = gen_trace_id()
        self._last_usage = {}  # ZZ-8: 每次调用 reset, 避免上次真实 usage 残留
        try:
            if request.input_text:
                out = self.generate(request.input_text, request)
            elif request.file_content and request.input_text:
                # 这种组合通常是：用input_text作为指令，file_content作为上下文；这里简单拼接
                fc = ensure_file_content_splits(request.file_content)
                ctx = "\n\n".join((fc.split_contents or [])[:5])
                out = self.generate(f"{request.input_text}\n\n[context]\n{ctx}", request)
            elif request.file_content:
                fc = ensure_file_content_splits(request.file_content)
                prompt = "\n\n".join((fc.split_contents or [])[:5])
                out = self.generate(prompt, request)
            else:
                out = ""
            _u = getattr(self, "_last_usage", None) or {}
            return LLMResponse(
                code="SUCCESS", message="ok", chat_result=out,
                prompt_tokens=_u.get("prompt_tokens"),
                completion_tokens=_u.get("completion_tokens"),
                total_tokens=_u.get("total_tokens"),
                cost_time=now_ts()-start, trace_id=trace_id,
            )
        except Exception as e:
            return LLMResponse(code="RAG_RUN_FAILED", message=str(e), cost_time=now_ts()-start, trace_id=trace_id)

    def chat_stream(self, messages: List[Dict[str, Any]], request: LLMRequest):
        """真正的 token-level 流式. yield 每个 delta.content (str).

        实现细节:
        - payload 加 stream=True
        - 用 requests stream=True + iter_lines 解析 OpenAI 兼容 SSE
        - 未配 api_key 时降级为 chat_with_context 的输出切片 (10 字符/段)
          以保证 generator 协议永远生效
        - 异常时 retry max_retry 次, 仍失败 raise
        """
        if not self.check_config():
            # mock 降级: 拿完整回复后切片 yield (共用 _BaseHTTPAdapterMixin._mock_stream)
            yield from self._mock_stream(messages, request)
            return

        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        p: LLMParam = request.model_param
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": p.temperature,
            "max_tokens": p.max_tokens,
            "stream": True,
        }
        if p.extra_params:
            payload.update(p.extra_params)

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retry):
            yielded = False
            try:
                for chunk in self._post_stream_openai(url, headers, payload, timeout=self.timeout):
                    yielded = True
                    yield chunk
                return
            except Exception as e:
                last_err = e
                # 已吐出 token 后不能重试: HTTP 流无法从中途续传, 重试会把前缀再吐一遍 → 输出重复错乱
                if yielded:
                    raise
                self.logger.warning(
                    f"[llm_adapter] OpenAI chat_stream attempt {attempt+1} failed: {e}",
                    logger_name="llm_adapter",
                )
                time.sleep(min(2 ** attempt, 8))
        if last_err:
            raise last_err
