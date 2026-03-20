from __future__ import annotations

import os
import time
import hashlib
from typing import Dict, Any, List, Optional, Tuple

import requests

from llm_adapter_module.core.base import (
    BaseLLMService,
    BaseVectorAdapter,
    BaseChatAdapter,
    BaseMultimodalAdapter,
)
from llm_adapter_module.model.data_model import (
    LLMRequest, LLMResponse, LLMParam,
    FileContent, MediaContent, MultimodalResult,
)
from llm_adapter_module.config.config import LLMAdapterConfig
from llm_adapter_module.utils.tool_functions import (
    gen_trace_id, now_ts,
    normalize_vector, ensure_file_content_splits,
    merge_media_from_file_and_request, hydrate_media_base64, safe_dict,
)

# 基础支撑层依赖（外部已实现）
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import ExceptionHandler, ConfigException, SystemBaseException


class _BaseHTTPAdapterMixin:
    """给需要HTTP调用的适配器提供通用requests能力（超时、重试）"""

    def _post_json(self, url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()


class OpenAIVectorAdapter(BaseVectorAdapter, _BaseHTTPAdapterMixin):
    """OpenAI 向量模型适配器（基于HTTP示例实现）

    说明：
    - 为了保持模块可独立测试，未强依赖openai官方SDK
    - 若你们项目已引入SDK，可在此替换实现
    """

    def __init__(self, model_name: str, model_cfg: Dict[str, Any], common_cfg: Dict[str, Any], logger: SystemLogger):
        self.model_name = model_name
        self.model_cfg = model_cfg
        self.common_cfg = common_cfg
        self.logger = logger
        self.api_key = str(model_cfg.get("api_key", "") or "")
        self.api_base = str(model_cfg.get("api_base", "https://api.openai.com/v1") or "").rstrip("/")
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
                # OpenAI embeddings: data['data'][i]['embedding']
                out: List[List[float]] = []
                for item in data.get("data", []):
                    out.append(item.get("embedding", []))
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


class OpenAIChatAdapter(BaseChatAdapter, _BaseHTTPAdapterMixin):
    """OpenAI 聊天模型适配器（HTTP示例实现，支持单轮与多轮）"""

    def __init__(self, model_name: str, model_cfg: Dict[str, Any], common_cfg: Dict[str, Any], logger: SystemLogger):
        self.model_name = model_name
        self.model_cfg = model_cfg
        self.common_cfg = common_cfg
        self.logger = logger
        self.api_key = str(model_cfg.get("api_key", "") or "")
        self.api_base = str(model_cfg.get("api_base", "https://api.openai.com/v1") or "").rstrip("/")
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
            return LLMResponse(code="SUCCESS", message="ok", chat_result=out, cost_time=now_ts()-start, trace_id=trace_id)
        except Exception as e:
            return LLMResponse(code="RAG_RUN_FAILED", message=str(e), cost_time=now_ts()-start, trace_id=trace_id)


class OpenAIMultimodalAdapter(BaseMultimodalAdapter, _BaseHTTPAdapterMixin):
    """OpenAI 多模态适配器（HTTP示例实现）

    说明：
    - OpenAI 多模态/视觉接口在不同版本API中路径与payload可能不同
    - 此处提供“可落地的框架 + 可测试的降级实现”
    - 若你们已确定厂商与具体API形态，请在此按实际接口调整
    """

    def __init__(self, model_name: str, model_cfg: Dict[str, Any], common_cfg: Dict[str, Any], logger: SystemLogger):
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
        # 注意：不同API可能不同；此处示例是“合理猜测的payload形态”，实际请按厂商文档调整。
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


class LLMService(BaseLLMService):
    """大模型统一服务实现：对外唯一入口"""

    def __init__(self):
        self.logger = SystemLogger()
        self.exception_handler = ExceptionHandler()
        self.config_manager = ConfigManager()
        self.config_manager.load_config()
        self.cfg = LLMAdapterConfig(self.config_manager)

        self.adapters: Dict[str, Any] = {}  # model_name -> adapter
        self.init_adapters()

    def init_adapters(self) -> None:
        # 从配置扫描所有模型
        llm_root = self.cfg.get_llm_root()
        common_cfg = self.cfg.get_common() if isinstance(self.cfg.get_common(), dict) else {}
        if not isinstance(llm_root, dict):
            llm_root = {}
        for vendor, vendor_cfg in llm_root.items():
            if vendor in {"default_vector_model", "default_chat_model", "default_multimodal_model", "common"}:
                continue
            if not isinstance(vendor_cfg, dict):
                continue
            for model_name, model_cfg in vendor_cfg.items():
                if not isinstance(model_cfg, dict):
                    continue
                req_type = str(model_cfg.get("request_type", "")).upper()
                adapter_class = str(model_cfg.get("adapter_class", ""))
                adapter = self._build_adapter(adapter_class, model_name, model_cfg, common_cfg)
                if adapter:
                    self.adapters[model_name] = adapter
                    self.logger.info(f"[llm_adapter] registered model={model_name} type={req_type} adapter={adapter_class}", logger_name="llm_adapter")

    def _build_adapter(self, adapter_class: str, model_name: str, model_cfg: Dict[str, Any], common_cfg: Dict[str, Any]):
        # 可按需扩展更多厂商/更多适配器
        mapping = {
            "OpenAIVectorAdapter": OpenAIVectorAdapter,
            "OpenAIChatAdapter": OpenAIChatAdapter,
            "OpenAIMultimodalAdapter": OpenAIMultimodalAdapter,
        }
        cls = mapping.get(adapter_class)
        if not cls:
            # 未识别适配器：跳过
            self.logger.warning(f"[llm_adapter] unknown adapter_class={adapter_class} for model={model_name}", logger_name="llm_adapter")
            return None
        return cls(model_name=model_name, model_cfg=model_cfg, common_cfg=common_cfg, logger=self.logger)

    def validate_request(self, request: LLMRequest) -> Tuple[bool, str]:
        rt = (request.request_type or "").upper()
        if rt not in {"VECTOR", "CHAT", "MULTIMODAL"}:
            return False, "request_type仅支持VECTOR/CHAT/MULTIMODAL"

        if not request.model_name or request.model_name == "default":
            # 允许default，后续会替换为配置默认模型
            pass

        if rt == "VECTOR":
            if not (request.input_text or request.batch_input or request.file_content):
                return False, "VECTOR请求需提供input_text/batch_input/file_content"
        elif rt == "CHAT":
            if not (request.input_text or request.file_content):
                return False, "CHAT请求需提供input_text或file_content"
        elif rt == "MULTIMODAL":
            has_media = bool(request.media_input) or bool(request.file_content and request.file_content.media_contents)
            if not has_media:
                return False, "多模态请求需提供媒体输入"
        return True, ""

    def _resolve_model_name(self, request: LLMRequest) -> str:
        name = request.model_name or "default"
        if name == "default":
            return self.cfg.get_default_model((request.request_type or "").upper())
        return name

    def _get_adapter(self, model_name: str):
        adapter = self.adapters.get(model_name)
        if adapter:
            return adapter
        # 未注册：尝试按配置单独构建一次（支持配置热加载后动态新增）
        model_cfg = self.cfg.get_model_config(model_name)
        common_cfg = self.cfg.get_common() if isinstance(self.cfg.get_common(), dict) else {}
        adapter_class = str(model_cfg.get("adapter_class", ""))
        if adapter_class:
            adapter = self._build_adapter(adapter_class, model_name, model_cfg, common_cfg)
            if adapter:
                self.adapters[model_name] = adapter
                return adapter
        return None

    def call_llm(self, request: LLMRequest) -> LLMResponse:
        start = now_ts()
        trace_id = gen_trace_id()
        ok, msg = self.validate_request(request)
        if not ok:
            return LLMResponse(code="PARAM_INVALID", message=msg, request_info={"trace_id": trace_id}, cost_time=now_ts()-start, trace_id=trace_id)

        model_name = self._resolve_model_name(request)
        adapter = self._get_adapter(model_name)
        if not adapter:
            return LLMResponse(code="MODEL_NOT_FOUND", message=f"未注册的模型名称：{model_name}", request_info={"trace_id": trace_id}, cost_time=now_ts()-start, trace_id=trace_id)

        # 补充 request_info
        request_info = {
            "request_type": request.request_type,
            "model_name": model_name,
            "trace_id": trace_id,
        }

        try:
            request.model_name = model_name
            resp: LLMResponse = adapter.call(request)
            # 强制带上 trace_id 与 request_info
            resp.trace_id = resp.trace_id or trace_id
            resp.request_info = resp.request_info or request_info
            return resp
        except SystemBaseException as se:
            # 按系统异常码封装
            err = self.exception_handler.handle_exception(se)
            return LLMResponse(code=err.get("code", "UNKNOWN_ERROR"), message=err.get("message", str(se)), request_info=request_info, cost_time=now_ts()-start, trace_id=trace_id)
        except Exception as e:
            # 未知异常
            err = self.exception_handler.handle_exception(e)
            return LLMResponse(code=err.get("code", "UNKNOWN_ERROR"), message=err.get("message", str(e)), request_info=request_info, cost_time=now_ts()-start, trace_id=trace_id)

    def call_by_file(self, file_content: FileContent, request_type: str, model_param: Any = None) -> LLMResponse:
        req = LLMRequest(
            request_type=request_type,
            file_content=file_content,
            model_name="default",
            model_param=model_param if isinstance(model_param, LLMParam) else (LLMParam() if model_param is None else LLMParam(**model_param)),
        )
        return self.call_llm(req)

    def generate(self, prompt: str, trace_id: str = None) -> str:
        """
        给上层统一使用的最小文本生成入口
        返回纯文本，不返回 LLMResponse 对象
        """
        request = LLMRequest(
            request_type="CHAT",
            input_text=prompt,
            model_name="default",
            model_param=LLMParam(),
        )

        resp = self.call_llm(request)

        # 成功时返回 chat_result
        if getattr(resp, "code", None) == "SUCCESS":
            return getattr(resp, "chat_result", "") or ""

        # 失败时直接抛异常，便于上层看见真实错误
        raise RuntimeError(f"{getattr(resp, 'code', 'UNKNOWN_ERROR')}: {getattr(resp, 'message', '')}")
