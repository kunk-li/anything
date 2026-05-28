# -*- coding: utf-8 -*-
"""
LLMService — 大模型统一服务实现, 是 BaseLLMService 唯一实现, 所有上层 (RAG /
Agent / Reranker / QueryRewriter) 都通过此入口调用 LLM.

Task NN (#74): 原 1261 行单文件 (5 adapter + 1 mixin + LLMService) 拆为:
    core/impl.py              ← LLMService 本身
    core/adapters/
        _http_mixin.py        _BaseHTTPAdapterMixin
        openai_vector.py
        openai_chat.py
        openai_multimodal.py
        anthropic_chat.py
        ollama_chat.py
本文件保留所有 adapter 类的 re-export, 保证老 import
    `from llm_adapter_module.core.impl import OpenAIChatAdapter`
仍可用, 0 调用方改动.
"""
from __future__ import annotations

import os
import time
import hashlib
from typing import Dict, Any, List, Optional, Tuple

import requests  # 保留 — 部分单测 patch llm_adapter_module.core.impl.requests.post

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

# 5 个 adapter + mixin 已拆到 .adapters/, 在此 re-export 保持老 API:
#   from llm_adapter_module.core.impl import OpenAIChatAdapter, ...
# 不会有人因为拆分而 import 失败.
from .adapters import (
    _BaseHTTPAdapterMixin,
    OpenAIVectorAdapter,
    OpenAIChatAdapter,
    OpenAIMultimodalAdapter,
    AnthropicChatAdapter,
    OllamaChatAdapter,
)


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
        """统一 LLM 调用入口. Task HH (#68): 失败时按 fallback chain 自动切换模型.

        fallback chain 解析顺序:
          1. request.model_param.fallback_models (调用方显式)
          2. config llm.<request_type>_fallbacks (yaml 配置)
          3. 同 request_type 的默认模型 (兜底)
        每个模型受 ModelHealthTracker 控制 — unhealthy 的会被跳过 (在冷却期内).
        """
        start = now_ts()
        trace_id = gen_trace_id()
        ok, msg = self.validate_request(request)
        if not ok:
            return LLMResponse(code="PARAM_INVALID", message=msg, request_info={"trace_id": trace_id}, cost_time=now_ts()-start, trace_id=trace_id)

        # 解析候选模型链 (主 + fallback)
        candidates = self._resolve_model_chain(request)
        if not candidates:
            return LLMResponse(code="MODEL_NOT_FOUND", message="没有可用的模型 (候选链为空)",
                              request_info={"trace_id": trace_id},
                              cost_time=now_ts()-start, trace_id=trace_id)

        # Task HH (#68): 健康追踪
        try:
            from llm_adapter_module.utils import get_health_tracker
            health = get_health_tracker()
        except Exception:
            health = None

        last_resp: Optional[LLMResponse] = None
        tried: List[str] = []
        for model_name in candidates:
            # 跳过 unhealthy 模型 (冷却中)
            if health and not health.is_available(model_name):
                self.logger.warning(
                    f"[fallback] 跳过 unhealthy 模型: {model_name}, trace_id={trace_id}"
                )
                continue

            adapter = self._get_adapter(model_name)
            if not adapter:
                if health:
                    health.record_failure(model_name, f"adapter not found")
                tried.append(model_name)
                continue

            request_info = {
                "request_type": request.request_type,
                "model_name": model_name,
                "trace_id": trace_id,
                "fallback_chain": candidates,
                "tried_models": tried + [model_name],
            }
            try:
                request.model_name = model_name
                resp: LLMResponse = adapter.call(request)
                resp.trace_id = resp.trace_id or trace_id
                resp.request_info = resp.request_info or request_info
                # 调成功 → 记 health success, 触发 usage tracker, 返回
                if resp.code == "SUCCESS":
                    if health:
                        health.record_success(model_name)
                    try:
                        self._record_usage_safe(request, resp, model_name)
                    except Exception as _track_err:
                        self.logger.warning(f"[usage-tracker] 记录失败 (忽略): {_track_err}")
                    return resp
                # 业务码非 SUCCESS → 标 failure 但继续 fallback
                if health:
                    health.record_failure(model_name, f"code={resp.code}: {resp.message[:100]}")
                self.logger.warning(
                    f"[fallback] 模型 {model_name} 返回 {resp.code}, "
                    f"尝试下一个; trace_id={trace_id}"
                )
                last_resp = resp
                tried.append(model_name)
                continue
            except SystemBaseException as se:
                if health:
                    health.record_failure(model_name, f"SysException: {se}")
                err = self.exception_handler.handle_exception(se)
                last_resp = LLMResponse(
                    code=err.get("code", "UNKNOWN_ERROR"),
                    message=err.get("message", str(se)),
                    request_info=request_info,
                    cost_time=now_ts() - start, trace_id=trace_id,
                )
                tried.append(model_name)
                continue
            except Exception as e:
                if health:
                    health.record_failure(model_name, f"Exception: {e}")
                err = self.exception_handler.handle_exception(e)
                last_resp = LLMResponse(
                    code=err.get("code", "UNKNOWN_ERROR"),
                    message=err.get("message", str(e)),
                    request_info=request_info,
                    cost_time=now_ts() - start, trace_id=trace_id,
                )
                tried.append(model_name)
                continue

        # 所有候选都失败 → 返回最后一个错误信封 (或合成 ALL_MODELS_FAILED)
        if last_resp is not None:
            if last_resp.request_info is None:
                last_resp.request_info = {}
            last_resp.request_info["fallback_exhausted"] = True
            last_resp.request_info["tried_models"] = tried
            return last_resp
        return LLMResponse(
            code="ALL_MODELS_FAILED",
            message=f"所有候选模型都不可用 (健康检查均失败): {candidates}",
            request_info={"trace_id": trace_id, "tried_models": tried},
            cost_time=now_ts() - start, trace_id=trace_id,
        )

    def _resolve_model_chain(self, request: LLMRequest) -> List[str]:
        """生成 [主模型, fallback1, fallback2, ...] 列表, 已去重."""
        seen = set()
        chain: List[str] = []

        def _add(name):
            if name and name not in seen:
                seen.add(name)
                chain.append(name)

        # 1. 主模型
        primary = self._resolve_model_name(request)
        _add(primary)

        # 2. request.model_param.fallback_models (显式)
        try:
            mp = request.model_param
            fallback_models = getattr(mp, "fallback_models", None) if mp else None
            if isinstance(fallback_models, (list, tuple)):
                for m in fallback_models:
                    _add(str(m))
        except Exception:
            pass

        # 3. config llm.{type}_fallbacks (yaml)
        try:
            req_type = (request.request_type or "").lower()
            config_key = f"llm.{req_type}_fallbacks"
            cfg_list = self.cfg.get_config(config_key, []) if hasattr(self.cfg, "get_config") else []
            if isinstance(cfg_list, list):
                for m in cfg_list:
                    _add(str(m))
        except Exception:
            pass

        return chain

    # _call_llm_legacy 删除 — call_llm 上面的 fallback 路径已覆盖所有异常分支.

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
