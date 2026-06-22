# -*- coding: utf-8 -*-
"""
OpenAIVectorAdapter — OpenAI / OpenAI 兼容 (DashScope 等) 向量 embedding 适配器.

Task NN (#74): 从 impl.py 单文件拆出.
"""
from __future__ import annotations

import time
import hashlib
from typing import Dict, Any, List, Optional

from llm_adapter_module.core.base import BaseVectorAdapter
from llm_adapter_module.model.data_model import LLMRequest, LLMResponse
from llm_adapter_module.utils.tool_functions import (
    gen_trace_id, now_ts,
    normalize_vector, ensure_file_content_splits,
)

from ._http_mixin import _BaseHTTPAdapterMixin, _sanitize_api_base


class OpenAIVectorAdapter(BaseVectorAdapter, _BaseHTTPAdapterMixin):
    """OpenAI 向量模型适配器（基于HTTP示例实现）

    说明：
    - 为了保持模块可独立测试，未强依赖openai官方SDK
    - 若你们项目已引入SDK，可在此替换实现
    """

    def __init__(self, model_name: str, model_cfg: Dict[str, Any], common_cfg: Dict[str, Any], logger: "SystemLogger"):
        self.model_name = model_name
        self.model_cfg = model_cfg
        self.common_cfg = common_cfg
        self.logger = logger
        self.api_key = str(model_cfg.get("api_key", "") or "")
        # Task XXXX-19: api_base 走 sanitizer
        self.api_base = _sanitize_api_base(
            model_cfg.get("api_base"), model_name=model_name, logger=logger,
        )
        self.timeout = int(common_cfg.get("timeout", 30))
        self.max_retry = int(common_cfg.get("max_retry", 3))

    def check_config(self) -> bool:
        return bool(self.api_key and self.api_base)

    def embed_single(self, text: str, request: LLMRequest) -> List[float]:
        vectors = self.embed_batch([text], request)
        return vectors[0] if vectors else []

    def embed_batch(self, texts: List[str], request: LLMRequest) -> List[List[float]]:
        # 若未配置key，则返回可测试的伪向量（稳定哈希），避免单测依赖外部网络
        if not self.check_config():
            return [self._fake_embedding(t, dim=8) for t in texts]

        url = f"{self.api_base}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "input": texts,
        }
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retry):
            try:
                data = self._post_json(url, headers, payload, timeout=self.timeout)
                # OpenAI embeddings: data['data'][i] = {'index': i, 'embedding': [...]}
                # API 不保证按输入顺序返回, 必须按 item['index'] 散列回原位,
                # 否则会把向量错配到别的文本上 (静默污染向量库).
                items = data.get("data", []) or []
                out: List[List[float]] = [[] for _ in texts]
                for pos, item in enumerate(items):
                    idx = item.get("index")
                    if not isinstance(idx, int) or idx < 0 or idx >= len(texts):
                        # index 缺失/越界时退回枚举顺序 (仅在 index 不可用时)
                        idx = pos
                    if idx < len(out):
                        out[idx] = item.get("embedding", []) or []
                # 任何一个槽位为空 (条目缺失 / index 越界 / embedding 空) 都视为
                # 部分响应, 抛错触发重试/上层回退, 而不是持久化错配或缺失的向量.
                if len(items) != len(texts) or any(not v for v in out):
                    raise RuntimeError(
                        f"OpenAI embeddings 返回与输入不匹配: 输入 {len(texts)} 条, "
                        f"返回 {len(items)} 条 (含空向量: {sum(1 for v in out if not v)})"
                    )
                return out
            except Exception as e:
                last_err = e
                self.logger.warning(f"[llm_adapter] OpenAI embed attempt {attempt+1} failed: {e}", logger_name="llm_adapter")
                time.sleep(min(2 ** attempt, 8))
        raise last_err or RuntimeError("embedding failed")

    def call(self, request: LLMRequest) -> LLMResponse:
        start = now_ts()
        trace_id = gen_trace_id()
        try:
            texts: List[str] = []
            if request.batch_input:
                texts = request.batch_input
            elif request.input_text:
                texts = [request.input_text]
            elif request.file_content:
                fc = ensure_file_content_splits(request.file_content)
                texts = fc.split_contents or []
            vectors = self.embed_batch(texts, request)
            if request.model_param.normalize:
                vectors = [normalize_vector(v) for v in vectors]
            return LLMResponse(code="SUCCESS", message="ok", vector_result=vectors, cost_time=now_ts()-start, trace_id=trace_id)
        except Exception as e:
            return LLMResponse(code="VECTOR_QUERY_FAILED", message=str(e), cost_time=now_ts()-start, trace_id=trace_id)

    @staticmethod
    def _fake_embedding(text: str, dim: int = 8) -> List[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # map bytes to [-1,1]
        vec = [((digest[i] / 255.0) * 2.0 - 1.0) for i in range(dim)]
        return vec
