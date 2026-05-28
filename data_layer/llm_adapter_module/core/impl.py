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

# 基础支撑层依赖
# 注: ConfigManager / SystemLogger / ExceptionHandler 由 deps 注入,不在此 import;
#     ConfigException / SystemBaseException 是异常类,本模块需要 catch,故保留 import。
from exception_module.core.impl import ConfigException, SystemBaseException
from deps_module import BasicDeps


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
            # mock 降级: 拿完整回复后切片 yield
            full = self.chat_with_context(messages, request)
            chunk_size = 10
            for i in range(0, len(full), chunk_size):
                yield full[i:i + chunk_size]
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
            try:
                yield from self._post_stream_openai(url, headers, payload, timeout=self.timeout)
                return
            except Exception as e:
                last_err = e
                self.logger.warning(
                    f"[llm_adapter] OpenAI chat_stream attempt {attempt+1} failed: {e}",
                    logger_name="llm_adapter",
                )
                time.sleep(min(2 ** attempt, 8))
        if last_err:
            raise last_err


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
        self.api_base = str(
            model_cfg.get("api_base", "https://api.anthropic.com") or ""
        ).rstrip("/")
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
            try:
                yield from self._post_stream_anthropic(url, headers, payload, timeout=self.timeout)
                return
            except Exception as e:
                last_err = e
                self.logger.warning(
                    f"[llm_adapter] Anthropic chat_stream attempt {attempt+1} failed: {e}",
                    logger_name="llm_adapter",
                )
                time.sleep(min(2 ** attempt, 8))
        if last_err:
            raise last_err


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
        self.api_base = str(
            model_cfg.get("api_base", "http://localhost:11434") or ""
        ).rstrip("/")
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
            full = self.chat_with_context(messages, request)
            for i in range(0, len(full), 10):
                yield full[i:i + 10]
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
            try:
                yield from self._post_stream_ollama(url, headers, payload, timeout=self.timeout)
                return
            except Exception as e:
                last_err = e
                self.logger.warning(
                    f"[llm_adapter] Ollama chat_stream attempt {attempt+1} failed: {e}",
                    logger_name="llm_adapter",
                )
                time.sleep(min(2 ** attempt, 4))
        if last_err:
            raise last_err


class LLMService(BaseLLMService):
    """大模型统一服务实现：对外唯一入口"""

    def __init__(self, deps: Optional[BasicDeps] = None):
        from deps_module import build_basic_deps
        deps = deps or build_basic_deps()
        self.logger = deps.logger
        self.exception_handler = deps.exception_handler
        self.config_manager = deps.config
        self.cfg = LLMAdapterConfig(self.config_manager)

        self.adapters: Dict[str, Any] = {}  # model_name -> adapter
        # 运行期注册的 model 配置 (备份 model_cfg, 让 list_models 能返回 api_base 等元数据)
        self._model_configs: Dict[str, Dict[str, Any]] = {}
        # 运行期默认模型覆盖 (request_type -> model_name), 优先于 yaml 配置
        self._default_overrides: Dict[str, str] = {}
        self.init_adapters()

    # ------------------------------------------------------------------
    # 运行期管理 API (供 ApiService /config/models 端点使用)
    # ------------------------------------------------------------------

    def _mask_key(self, key: str) -> str:
        """脱敏: sk-1234567890abcdef -> sk-****cdef (只留首 3 + 末 4)"""
        if not key:
            return ""
        s = str(key)
        if len(s) <= 8:
            return "****"
        return f"{s[:3]}****{s[-4:]}"

    def list_models(self, *, mask_keys: bool = True) -> List[Dict[str, Any]]:
        """返回当前注册的所有模型元数据.

        Args:
            mask_keys: 默认 True, 把 api_key 脱敏 (sk-***cdef);
                       设 False 返回原文 (只该用在内部审计/测试)。
        """
        # 真实记录的 cfg 优先, 否则从 cfg 配置回读
        out: List[Dict[str, Any]] = []
        for name, adapter in self.adapters.items():
            mcfg = self._model_configs.get(name) or getattr(adapter, "model_cfg", {}) or {}
            req_type = str(mcfg.get("request_type", "")).upper()
            api_key = mcfg.get("api_key") or getattr(adapter, "api_key", "") or ""
            api_base = mcfg.get("api_base") or getattr(adapter, "api_base", "") or ""
            adapter_class = str(mcfg.get("adapter_class", "") or type(adapter).__name__)
            entry = {
                "name": name,
                "request_type": req_type,
                "adapter_class": adapter_class,
                "api_base": api_base,
                "api_key": self._mask_key(api_key) if mask_keys else api_key,
                "configured": bool(api_key and api_base),
                "is_default": self.get_default_model(req_type) == name,
            }
            out.append(entry)
        # 按 request_type 然后 name 排序, 让 UI 显示稳定
        return sorted(out, key=lambda e: (e["request_type"], e["name"]))

    def get_default_model(self, request_type: str) -> str:
        """读默认模型: 运行期覆盖优先, 否则走 yaml 配置 (cfg.get_default_model)."""
        rt = (request_type or "").upper()
        if rt in self._default_overrides:
            return self._default_overrides[rt]
        try:
            return self.cfg.get_default_model(rt) or ""
        except Exception:
            return ""

    def register_or_update_model(
        self,
        name: str,
        request_type: str,
        adapter_class: str,
        api_key: str,
        api_base: str,
        *,
        extra: Optional[Dict[str, Any]] = None,
        set_as_default: bool = False,
    ) -> Dict[str, Any]:
        """运行期注册或更新一个模型. 返回脱敏后的条目.

        约束:
            - name: 非空, 用于 _resolve_model_name 查表
            - request_type: VECTOR / CHAT / MULTIMODAL 之一
            - adapter_class: 必须在 _build_adapter mapping 里 (OpenAI* 三种之一)
            - api_key / api_base: 缺一则 check_config = False (适配器走 mock)

        side-effect:
            - 覆盖 self.adapters[name] (旧实例被丢弃)
            - 写 self._model_configs[name]
            - set_as_default=True 时更新 self._default_overrides[request_type]
        """
        if not name:
            raise ValueError("model name 不能为空")
        rt = str(request_type or "").upper()
        if rt not in {"VECTOR", "CHAT", "MULTIMODAL"}:
            raise ValueError(f"request_type 仅支持 VECTOR/CHAT/MULTIMODAL, 收到: {request_type!r}")

        model_cfg = {
            "request_type": rt,
            "adapter_class": adapter_class,
            "api_key": api_key or "",
            "api_base": api_base or "",
        }
        if extra:
            model_cfg.update(extra)

        common_cfg = self.cfg.get_common() if isinstance(self.cfg.get_common(), dict) else {}
        adapter = self._build_adapter(adapter_class, name, model_cfg, common_cfg)
        if adapter is None:
            raise ValueError(f"无法构造适配器: adapter_class={adapter_class!r}")

        self.adapters[name] = adapter
        self._model_configs[name] = model_cfg
        if set_as_default:
            self._default_overrides[rt] = name

        self.logger.info(
            f"[llm_adapter] runtime register model={name} type={rt} adapter={adapter_class} "
            f"default={set_as_default} key={'***'+str(api_key)[-4:] if api_key else 'empty'}",
            logger_name="llm_adapter",
        )
        return self.list_models()[
            next((i for i, e in enumerate(self.list_models()) if e["name"] == name), 0)
        ]

    def unregister_model(self, name: str) -> bool:
        """从 adapters / _model_configs 移除. 若该模型是某 request_type 的默认, 同步清覆盖."""
        existed = name in self.adapters
        self.adapters.pop(name, None)
        self._model_configs.pop(name, None)
        # 清掉运行期覆盖 (如果它是)
        for rt, mname in list(self._default_overrides.items()):
            if mname == name:
                self._default_overrides.pop(rt, None)
        if existed:
            self.logger.info(f"[llm_adapter] unregistered model={name}", logger_name="llm_adapter")
        return existed

    def set_default_model(self, name: str, request_type: Optional[str] = None) -> Dict[str, Any]:
        """把 name 设为 request_type 的默认. request_type 不传时从已注册条目推导."""
        if name not in self.adapters:
            raise ValueError(f"model {name!r} 未注册")
        rt = (request_type or "").upper()
        if not rt:
            # 推导
            mcfg = self._model_configs.get(name) or getattr(self.adapters[name], "model_cfg", {}) or {}
            rt = str(mcfg.get("request_type", "")).upper()
        if rt not in {"VECTOR", "CHAT", "MULTIMODAL"}:
            raise ValueError(f"无法推导 request_type, 请显式传参")
        self._default_overrides[rt] = name
        self.logger.info(f"[llm_adapter] set default {rt}={name}", logger_name="llm_adapter")
        return {"request_type": rt, "default_model": name}

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
            # 扩展厂商:
            "AnthropicChatAdapter": AnthropicChatAdapter,
            "OllamaChatAdapter": OllamaChatAdapter,
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
            # PR: 优先用运行期 default override, 否则回退 yaml 配置
            rt = (request.request_type or "").upper()
            override = self._default_overrides.get(rt)
            if override:
                return override
            return self.cfg.get_default_model(rt)
        return name

    def _record_usage_safe(
        self, request: "LLMRequest", resp: "LLMResponse", model_name: str
    ) -> None:
        """Task Y (#59): 把这次调用喂给 UsageTracker. adapter 没填 tokens 就估算.

        估算规则: 4 字符 ≈ 1 token (业内常用近似). 输入用 input_text,
        输出用 chat_result / vector_result (count*dim) / multimodal_result.text.
        """
        try:
            from observability_module import get_usage_tracker, get_current_tenant
        except Exception:
            return
        # adapter 已经填了就尊重它
        prompt_t = resp.prompt_tokens
        completion_t = resp.completion_tokens
        if prompt_t is None:
            input_chars = 0
            if request.input_text:
                input_chars += len(request.input_text)
            if request.batch_input:
                input_chars += sum(len(s or "") for s in request.batch_input)
            prompt_t = max(0, input_chars // 4)
        if completion_t is None:
            output_chars = 0
            if resp.chat_result:
                output_chars += len(resp.chat_result)
            elif resp.vector_result:
                # 向量调用没"输出文本", 用 vector 维度 * batch 当作近似 token
                output_chars = sum(len(v or []) for v in resp.vector_result)
            elif resp.multimodal_result and resp.multimodal_result.text_result:
                output_chars += len(resp.multimodal_result.text_result)
            completion_t = max(0, output_chars // 4)
        # 回填到 response, 方便上层观察
        if resp.prompt_tokens is None:
            resp.prompt_tokens = prompt_t
        if resp.completion_tokens is None:
            resp.completion_tokens = completion_t
        if resp.total_tokens is None:
            resp.total_tokens = prompt_t + completion_t

        tracker = get_usage_tracker()
        try:
            tenant = get_current_tenant()
        except Exception:
            tenant = None
        record = tracker.record(
            model_name=model_name,
            prompt_tokens=prompt_t,
            completion_tokens=completion_t,
            tenant_id=tenant,
            trace_id=resp.trace_id,
        )
        if resp.cost_usd is None:
            resp.cost_usd = record.get("cost_usd")

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
            # Task Y (#59): token / cost 跟踪
            # adapter 没填 tokens 就用 4 字符 ≈ 1 token 简易估算 (中文 2 字符 ≈ 1 token,
            # 平均 3 字符更稳, 但 4 是行业默认; chat_stream 路径目前不进 call_llm 不影响).
            try:
                self._record_usage_safe(request, resp, model_name)
            except Exception as _track_err:
                self.logger.warning(f"[usage-tracker] 记录失败 (忽略): {_track_err}")
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

    def chat_stream(
        self,
        prompt: str,
        model_name: str = "default",
        trace_id: Optional[str] = None,
    ):
        """真正 token-level 流式. yield 每个 delta token / chunk (str).

        - 自动按 model_name 路由到对应 adapter; "default" 走 yaml 默认 chat 模型
        - 仅当 adapter 暴露了 chat_stream 方法 (OpenAI 系列) 走真实 SSE 流;
          其他 adapter (Anthropic/Ollama 等本期未实现) 自动降级为 generate +
          单 chunk yield, 调用方仍是 generator 接口不破坏。
        - 失败抛 RuntimeError (不像 generate 那样会兜底返回错误字符串)
        """
        request = LLMRequest(
            request_type="CHAT",
            input_text=prompt,
            model_name=model_name,
            model_param=LLMParam(),
        )
        resolved_name = self._resolve_model_name(request)
        adapter = self._get_adapter(resolved_name)
        if adapter is None:
            raise RuntimeError(f"MODEL_NOT_FOUND: {resolved_name}")
        request.model_name = resolved_name

        if hasattr(adapter, "chat_stream"):
            messages = [{"role": "user", "content": prompt}]
            yield from adapter.chat_stream(messages, request)
        else:
            # 降级: adapter 不支持 stream, 一次性拿完整文本作为一个 chunk yield
            resp = adapter.call(request)
            text = getattr(resp, "chat_result", "") or ""
            if text:
                yield text

    def generate(self, prompt: str, trace_id: Optional[str] = None) -> str:
        """统一文本生成入口（实现 BaseLLMService.generate 契约）。

        返回纯文本，不返回 LLMResponse 对象。失败时直接抛 RuntimeError。
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
