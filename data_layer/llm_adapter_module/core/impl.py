from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Type

import requests

from llm_adapter_module.config.config import LLMAdapterConfig
from llm_adapter_module.core.base import (
    BaseLLMService,
    BaseVectorAdapter,
    BaseChatAdapter,
    BaseMultimodalAdapter,
)
from llm_adapter_module.model.data_model import (
    LLMRequest,
    LLMResponse,
    LLMParam,
    FileContent,
    MediaContent,
    MultimodalResult,
)
from llm_adapter_module.utils.tool_functions import (
    ensure_filecontent_splits,
    normalize_vectors,
    validate_media_list,
    to_openai_image_part,
)

from log_module.core.impl import SystemLogger
from exception_module.core.impl import ExceptionHandler, ConfigException, SystemBaseException


# ----------------------------- OpenAI adapters -----------------------------

class _OpenAIBase:
    def __init__(self, model_name: str, model_cfg: Dict[str, Any], common_timeout: int):
        self.model_name = model_name
        self.model_cfg = model_cfg or {}
        self.api_key = self.model_cfg.get("api_key")
        self.api_base = self.model_cfg.get("api_base", "https://api.openai.com/v1").rstrip("/")
        self.timeout = int(self.model_cfg.get("timeout", common_timeout))
        self.support_media = self.model_cfg.get("support_media", [])
        self.max_media_size = self.model_cfg.get("max_media_size", None)

    def check_config(self) -> bool:
        return bool(self.api_key and self.api_base)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.api_base}{path}"
        r = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout)
        r.raise_for_status()
        return r.json()


class OpenAIVectorAdapter(BaseVectorAdapter, _OpenAIBase):
    """OpenAI 向量模型适配器（REST /embeddings）"""

    def __init__(self, model_name: str, model_cfg: Dict[str, Any], common_timeout: int = 30):
        BaseVectorAdapter.__init__(self, model_name)
        _OpenAIBase.__init__(self, model_name, model_cfg, common_timeout)

    def embed_single(self, text: str, request: LLMRequest) -> List[float]:
        res = self.embed_batch([text], request)
        return res[0] if res else []

    def embed_batch(self, texts: List[str], request: LLMRequest) -> List[List[float]]:
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "input": texts,
        }
        if request.model_param and request.model_param.extra_params:
            payload.update(request.model_param.extra_params)
        data = self._post("/embeddings", payload)
        vectors = [item["embedding"] for item in data.get("data", [])]
        if request.model_param.normalize:
            vectors = normalize_vectors(vectors)
        return vectors

    def call(self, request: LLMRequest) -> LLMResponse:
        # service 里会统一封装，adapter 只做核心业务
        texts: List[str] = []
        if request.batch_input:
            texts = request.batch_input
        elif request.input_text:
            texts = [request.input_text]
        elif request.file_content and request.file_content.split_contents:
            texts = request.file_content.split_contents
        elif request.file_content and request.file_content.text_content:
            texts = [request.file_content.text_content]
        vectors = self.embed_batch(texts, request)
        return LLMResponse(code="SUCCESS", message="ok", vector_result=vectors)

class OpenAIChatAdapter(BaseChatAdapter, _OpenAIBase):
    """OpenAI 聊天模型适配器（REST /chat/completions）"""

    def __init__(self, model_name: str, model_cfg: Dict[str, Any], common_timeout: int = 30):
        BaseChatAdapter.__init__(self, model_name)
        _OpenAIBase.__init__(self, model_name, model_cfg, common_timeout)

    def generate(self, prompt: str, request: LLMRequest) -> str:
        messages = [{"role": "user", "content": prompt}]
        return self.chat_with_context(messages, request)

    def chat_with_context(self, messages: List[Dict[str, Any]], request: LLMRequest) -> str:
        mp = request.model_param or LLMParam()
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": mp.temperature,
            "max_tokens": mp.max_tokens,
        }
        # OpenAI chat/completions 不支持 top_k，保留 extra_params 可扩展
        if mp.extra_params:
            payload.update(mp.extra_params)
        data = self._post("/chat/completions", payload)
        choices = data.get("choices", [])
        if not choices:
            return ""
        msg = choices[0].get("message", {})
        return msg.get("content", "") or ""

    def call(self, request: LLMRequest) -> LLMResponse:
        if request.messages:
            out = self.chat_with_context(request.messages, request)
        else:
            prompt = request.input_text or (request.file_content.text_content if request.file_content else "") or ""
            out = self.generate(prompt, request)
        return LLMResponse(code="SUCCESS", message="ok", chat_result=out)

class OpenAIMultimodalAdapter(BaseMultimodalAdapter, _OpenAIBase):
    """OpenAI 多模态模型适配器（chat/completions with image parts; audio optional）"""

    def __init__(self, model_name: str, model_cfg: Dict[str, Any], common_timeout: int = 30):
        BaseMultimodalAdapter.__init__(self, model_name)
        _OpenAIBase.__init__(self, model_name, model_cfg, common_timeout)

    def understand_text_media(self, text: str, media_list: List[MediaContent], request: LLMRequest) -> MultimodalResult:
        ok, msg = validate_media_list(media_list, support_media=self.support_media, max_media_size_mb=self.max_media_size)
        if not ok:
            raise ValueError(msg)

        content_parts: List[Dict[str, Any]] = [{"type": "text", "text": text or ""}]
        for m in media_list:
            if m.media_type == "image":
                content_parts.append(to_openai_image_part(m))
            elif m.media_type == "audio":
                # 音频理解在 OpenAI 生态中通常走转写或专用接口；这里做降级：先转写再问答
                transcript = self.media_to_text([m], request)
                content_parts.append({"type": "text", "text": f"[audio_transcript]\n{transcript}"})
            else:
                raise ValueError(f"暂不支持的媒体类型：{m.media_type}")

        mp = request.model_param or LLMParam()
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": content_parts}],
            "temperature": mp.temperature,
            "max_tokens": mp.max_tokens,
        }
        if mp.extra_params:
            payload.update(mp.extra_params)
        data = self._post("/chat/completions", payload)
        choices = data.get("choices", [])
        text_out = ""
        if choices:
            text_out = (choices[0].get("message", {}) or {}).get("content", "") or ""
        return MultimodalResult(text_result=text_out)

    def media_to_text(self, media_list: List[MediaContent], request: LLMRequest) -> str:
        # 尝试使用 OpenAI 音频转写接口：/audio/transcriptions
        # 仅当 media_type == audio 且配置中存在 transcription_model 时启用
        if not media_list:
            return ""
        m = media_list[0]
        if m.media_type != "audio":
            raise ValueError("media_to_text currently only supports audio -> text")
        transcription_model = self.model_cfg.get("transcription_model")
        if not transcription_model:
            raise ValueError("未配置 transcription_model，无法进行音频转写")
        url = f"{self.api_base}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": open(m.media_path, "rb")}
        data = {"model": transcription_model}
        r = requests.post(url, headers=headers, files=files, data=data, timeout=self.timeout)
        r.raise_for_status()
        j = r.json()
        return j.get("text", "") or ""

    def multimodal_chat(self, messages: List[Dict[str, Any]], request: LLMRequest) -> MultimodalResult:
        # messages: [{"role":"user","content":"...","media":[MediaContent,...]}]
        converted: List[Dict[str, Any]] = []
        for msg in messages or []:
            role = msg.get("role", "user")
            text = msg.get("content", "") or ""
            media_list = msg.get("media") or []
            parts: List[Dict[str, Any]] = [{"type": "text", "text": text}]
            for m in media_list:
                if isinstance(m, dict):
                    m = MediaContent(**m)  # 容错：允许 dict 形式
                if m.media_type == "image":
                    parts.append(to_openai_image_part(m))
                elif m.media_type == "audio":
                    transcript = self.media_to_text([m], request)
                    parts.append({"type": "text", "text": f"[audio_transcript]\n{transcript}"})
                else:
                    raise ValueError(f"暂不支持的媒体类型：{m.media_type}")
            converted.append({"role": role, "content": parts})

        mp = request.model_param or LLMParam()
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": converted,
            "temperature": mp.temperature,
            "max_tokens": mp.max_tokens,
        }
        if mp.extra_params:
            payload.update(mp.extra_params)
        data = self._post("/chat/completions", payload)
        choices = data.get("choices", [])
        text_out = ""
        if choices:
            text_out = (choices[0].get("message", {}) or {}).get("content", "") or ""
        return MultimodalResult(text_result=text_out)

    def call(self, request: LLMRequest) -> LLMResponse:
        media_list = request.media_input or (request.file_content.media_contents if request.file_content else None) or []
        if request.messages:
            mm = self.multimodal_chat(request.messages, request)
        else:
            mm = self.understand_text_media(request.input_text or "", media_list, request)
        return LLMResponse(code="SUCCESS", message="ok", multimodal_result=mm)


# ----------------------------- service -----------------------------

_ADAPTER_CLASS_REGISTRY: Dict[str, Type] = {
    "OpenAIVectorAdapter": OpenAIVectorAdapter,
    "OpenAIChatAdapter": OpenAIChatAdapter,
    "OpenAIMultimodalAdapter": OpenAIMultimodalAdapter,
}

class LLMService(BaseLLMService):
    """模块对外唯一核心服务：统一初始化、路由、校验、异常封装。"""

    def __init__(self):
        self.logger = SystemLogger()
        self.exc = ExceptionHandler()
        self.cfg = LLMAdapterConfig()
        self.common = self.cfg.get_common()
        self.adapters: Dict[str, Any] = {}  # model_name -> adapter instance
        self.model_type: Dict[str, str] = {}  # model_name -> request_type
        self._initialized = False
        self.init_adapters()

    def init_adapters(self) -> None:
        if self._initialized:
            return
        llm_root = self.cfg.get_llm_root()
        # 遍历厂商节点：openai / zhipu / etc
        for vendor, vendor_cfg in (llm_root or {}).items():
            if vendor in {"common", "default_vector_model", "default_chat_model", "default_multimodal_model"}:
                continue
            if not isinstance(vendor_cfg, dict):
                continue
            for model_name, model_cfg in vendor_cfg.items():
                if not isinstance(model_cfg, dict):
                    continue
                adapter_class_name = model_cfg.get("adapter_class")
                request_type = model_cfg.get("request_type")
                if not adapter_class_name or not request_type:
                    continue
                cls = _ADAPTER_CLASS_REGISTRY.get(adapter_class_name)
                if not cls:
                    self.logger.warning(f"未识别的 adapter_class：{adapter_class_name}，model={model_name}")
                    continue
                try:
                    adapter = cls(model_name=model_name, model_cfg=model_cfg, common_timeout=self.common.timeout)
                    if not adapter.check_config():
                        self.logger.warning(f"模型配置不完整，跳过注册：{model_name}")
                        continue
                    self.adapters[model_name] = adapter
                    self.model_type[model_name] = request_type
                    self.logger.info(f"已注册模型适配器：{model_name} ({request_type})")
                except Exception as e:
                    self.logger.error(f"注册模型失败：{model_name}，错误：{e}")
        self._initialized = True

    def _pick_model(self, request: LLMRequest) -> str:
        # default 兜底
        model_name = request.model_name or "default"
        if model_name == "default":
            model_name = self.cfg.get_default_model(request.request_type)
        return model_name

    def validate_request(self, request: LLMRequest):
        if request is None:
            return False, "request 不能为空"
        if request.request_type not in {"VECTOR", "CHAT", "MULTIMODAL"}:
            return False, f"request_type 不支持：{request.request_type}"
        if request.request_type == "VECTOR":
            if request.batch_input:
                return True, ""
            if request.input_text:
                return True, ""
            if request.file_content and (request.file_content.split_contents or request.file_content.text_content):
                return True, ""
            return False, "向量请求需提供 input_text / batch_input / file_content(text_content|split_contents)"
        if request.request_type == "CHAT":
            if request.messages:
                return True, ""
            if request.input_text:
                return True, ""
            if request.file_content and request.file_content.text_content:
                return True, ""
            return False, "聊天请求需提供 input_text / messages / file_content.text_content"
        if request.request_type == "MULTIMODAL":
            if request.messages:
                return True, ""
            media_list = request.media_input or (request.file_content.media_contents if request.file_content else None)
            if media_list:
                return True, ""
            return False, "多模态请求需提供 media_input 或 file_content.media_contents（或 messages 带 media）"
        return True, ""

    def call_by_file(self, file_content: FileContent, request_type: str, model_param=None, model_name: str = "default") -> LLMResponse:
        req = LLMRequest(
            request_type=request_type,
            file_content=file_content,
            model_name=model_name or "default",
            model_param=model_param or LLMParam(),
        )
        return self.call_llm(req)

    def call_llm(self, request: LLMRequest) -> LLMResponse:
        trace_id = str(uuid.uuid4())
        start = time.time()
        # request 校验
        ok, msg = self.validate_request(request)
        if not ok:
            return LLMResponse(code="PARAM_INVALID", message=msg, trace_id=trace_id, cost_time=round(time.time()-start, 6))

        # 文件输入适配：补全 split_contents
        if request.file_content and request.request_type in {"VECTOR"}:
            ensure_filecontent_splits(request.file_content, max_chars=2000, overlap=200)

        model_name = self._pick_model(request)
        adapter = self.adapters.get(model_name)
        if not adapter:
            # 配置缺失
            err = ConfigException("CONFIG_KEY_MISSING", f"未注册的模型名称：{model_name}")
            info = self.exc.handle_exception(err)
            return LLMResponse(code=info["code"], message=info["message"], trace_id=trace_id, cost_time=round(time.time()-start, 6), request_info={"model_name": model_name})

        try:
            resp = adapter.call(request)
            resp.trace_id = trace_id
            resp.cost_time = round(time.time() - start, 6)
            # 补充 request_info 便于调试
            resp.request_info = resp.request_info or {
                "request_type": request.request_type,
                "model_name": model_name,
            }
            return resp
        except requests.Timeout as e:
            info = self.exc.handle_exception(SystemBaseException("SYSTEM_ERROR", f"大模型调用超时：{e}"))
            return LLMResponse(code=info["code"], message=info["message"], trace_id=trace_id, cost_time=round(time.time()-start, 6))
        except requests.HTTPError as e:
            # HTTPError 通常带 response，尽量给出状态码
            status = getattr(getattr(e, "response", None), "status_code", None)
            msg = f"大模型HTTP错误{status}: {e}"
            info = self.exc.handle_exception(SystemBaseException("SYSTEM_ERROR", msg))
            return LLMResponse(code=info["code"], message=info["message"], trace_id=trace_id, cost_time=round(time.time()-start, 6))
        except Exception as e:
            info = self.exc.handle_exception(e)
            return LLMResponse(code=info["code"], message=info["message"], trace_id=trace_id, cost_time=round(time.time()-start, 6))
