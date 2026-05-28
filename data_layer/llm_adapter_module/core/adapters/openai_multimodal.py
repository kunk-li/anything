# -*- coding: utf-8 -*-
"""
OpenAIMultimodalAdapter — OpenAI 多模态 (vision) 适配器,
                         understand_text_media / multimodal_chat / call.

Task NN (#74): 从 impl.py 单文件拆出.
"""
from __future__ import annotations

import os
import time
from typing import Dict, Any, List, Optional

from llm_adapter_module.core.base import BaseMultimodalAdapter
from llm_adapter_module.model.data_model import (
    LLMRequest, LLMResponse, MediaContent, MultimodalResult,
)
from llm_adapter_module.utils.tool_functions import (
    gen_trace_id, now_ts,
    merge_media_from_file_and_request, hydrate_media_base64,
)

from ._http_mixin import _BaseHTTPAdapterMixin


class OpenAIMultimodalAdapter(BaseMultimodalAdapter, _BaseHTTPAdapterMixin):
    """OpenAI 多模态适配器（HTTP示例实现）

    说明：
    - OpenAI 多模态/视觉接口在不同版本API中路径与payload可能不同
    - 此处提供"可落地的框架 + 可测试的降级实现"
    - 若你们已确定厂商与具体API形态，请在此按实际接口调整
    """

    def __init__(self, model_name: str, model_cfg: Dict[str, Any], common_cfg: Dict[str, Any], logger: "SystemLogger"):
        self.model_name = model_name
        self.model_cfg = model_cfg
        self.common_cfg = common_cfg
        self.logger = logger
        self.api_key = str(model_cfg.get("api_key", "") or "")
        self.api_base = str(model_cfg.get("api_base", "https://api.openai.com/v1") or "").rstrip("/")
        self.timeout = int(common_cfg.get("timeout", 30))
        self.max_retry = int(common_cfg.get("max_retry", 3))
        self.support_media = list(model_cfg.get("support_media", ["image"]))
        self.max_media_size_mb = float(model_cfg.get("max_media_size", 20))

    def check_config(self) -> bool:
        return bool(self.api_key and self.api_base)

    def _max_bytes(self) -> int:
        return int(self.max_media_size_mb * 1024 * 1024)

    def media_to_text(self, media_list: List[MediaContent], request: LLMRequest) -> str:
        # 降级：若无真实多模态接口，这里只返回媒体元信息汇总
        parts: List[str] = []
        for m in media_list:
            meta = m.media_metadata or {}
            parts.append(f"{m.media_type}:{os.path.basename(m.media_path)} meta={meta}")
        return "\n".join(parts)

    def understand_text_media(self, text: str, media_list: List[MediaContent], request: LLMRequest) -> MultimodalResult:
        if not self.check_config():
            # mock返回：把文本与媒体描述拼接
            mtxt = self.media_to_text(media_list, request)
            return MultimodalResult(text_result=f"[mock-multimodal:{self.model_name}] {text}\n{mtxt}", confidence=0.5)

        # 真实调用（示例）：构造 content 数组 (text + image_url/base64)
        # 注意：不同API可能不同；此处示例是"合理猜测的payload形态"，实际请按厂商文档调整。
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        content: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        for m in media_list:
            if m.media_type not in self.support_media:
                raise ValueError(f"unsupported media_type: {m.media_type}")
            hydrate_media_base64(m, max_bytes=self._max_bytes())
            if not m.media_base64:
                raise ValueError(f"missing media_base64 for {m.media_path}")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{m.media_base64}"}
            })
        p = request.model_param
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": content}],
            "temperature": p.temperature,
            "max_tokens": p.max_tokens,
        }
        if p.extra_params:
            payload.update(p.extra_params)

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retry):
            try:
                data = self._post_json(url, headers, payload, timeout=self.timeout)
                choices = data.get("choices", [])
                msg = choices[0].get("message", {}) if choices else {}
                txt = msg.get("content", "") or ""
                return MultimodalResult(text_result=txt, confidence=None)
            except Exception as e:
                last_err = e
                self.logger.warning(f"[llm_adapter] OpenAI multimodal attempt {attempt+1} failed: {e}", logger_name="llm_adapter")
                time.sleep(min(2 ** attempt, 8))
        raise last_err or RuntimeError("multimodal failed")

    def multimodal_chat(self, messages: List[Dict[str, Any]], request: LLMRequest) -> MultimodalResult:
        # 简化：将最后一条消息中的媒体+文本调用 understand_text_media
        if not messages:
            return MultimodalResult(text_result="", confidence=None)
        last = messages[-1]
        text = str(last.get("content", "") or "")
        media = last.get("media", []) or []
        media_list: List[MediaContent] = media
        return self.understand_text_media(text, media_list, request)

    def call(self, request: LLMRequest) -> LLMResponse:
        start = now_ts()
        trace_id = gen_trace_id()
        try:
            media_list = merge_media_from_file_and_request(
                request.file_content.media_contents if request.file_content else None,
                request.media_input,
            )
            if not media_list:
                raise ValueError("多模态请求需提供媒体输入")
            text = request.input_text or ""
            result = self.understand_text_media(text, media_list, request)
            return LLMResponse(code="SUCCESS", message="ok", multimodal_result=result, cost_time=now_ts()-start, trace_id=trace_id)
        except Exception as e:
            return LLMResponse(code="RAG_RUN_FAILED", message=str(e), cost_time=now_ts()-start, trace_id=trace_id)
