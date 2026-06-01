# -*- coding: utf-8 -*-
"""
_BaseHTTPAdapterMixin — 给需要 HTTP 调用的适配器提供通用 requests 能力
                       (POST JSON + 三家 SSE / NDJSON 流式 generator)

Task NN (#74): 从 impl.py 1261 行单文件拆出, 让 5 个适配器各自单文件依赖此 mixin.
"""
from __future__ import annotations

import os
import re
from typing import Dict, Any

import requests


# Task XXXX-19: api_base sanitize + 智能默认
# 当 model_cfg 给的 api_base 是空 / "undefined" / "null" / 不带 http(s)://
# 时, 不再让 requests 拼出 "https://undefined/..." 然后 host 超时,
# 而是按优先级选个合理的默认:
#   1. 环境变量 (OPENAI_API_BASE / DASHSCOPE_API_BASE)
#   2. 模型名启发 (qwen* → DashScope, 其他 → OpenAI)
#   3. 硬编码 OpenAI 兼容默认
_INVALID_API_BASE_PATTERN = re.compile(r"^\s*(undefined|null|none|nan|)\s*$", re.I)


def _sanitize_api_base(
    api_base: str,
    model_name: str = "",
    logger=None,
    *,
    adapter_kind: str = "openai",
) -> str:
    """把 api_base 归一化成合法 http(s):// URL. 失败值走启发式默认.

    Args:
        adapter_kind: "openai" | "anthropic" | "ollama"
            决定默认值方向 (不同 provider 默认 endpoint 不同)
    Returns:
        always a valid http(s):// URL (rstrip "/" 后), 永不为空 / "undefined".
    """
    raw = str(api_base or "").strip()
    bad = False
    if not raw or _INVALID_API_BASE_PATTERN.match(raw):
        bad = True
    elif not (raw.startswith("http://") or raw.startswith("https://")):
        bad = True
    elif "//undefined" in raw or "//null" in raw:  # 防 "https://undefined" 这种
        bad = True

    if not bad:
        return raw.rstrip("/")

    # ---------- Anthropic 兜底 ----------
    if adapter_kind == "anthropic":
        env_base = (os.environ.get("ANTHROPIC_API_BASE") or "").strip()
        if env_base.startswith(("http://", "https://")):
            target = env_base
        else:
            target = "https://api.anthropic.com"
        if logger:
            try:
                logger.warning(f"[llm_adapter:anthropic] api_base 非法 ({api_base!r}), fallback {target!r}")
            except Exception:
                pass
        return target.rstrip("/")

    # ---------- Ollama 兜底 ----------
    if adapter_kind == "ollama":
        env_base = (os.environ.get("OLLAMA_API_BASE") or "").strip()
        if env_base.startswith(("http://", "https://")):
            target = env_base
        else:
            target = "http://localhost:11434"
        if logger:
            try:
                logger.warning(f"[llm_adapter:ollama] api_base 非法 ({api_base!r}), fallback {target!r}")
            except Exception:
                pass
        return target.rstrip("/")

    # ---------- OpenAI 系 (默认) 兜底 ----------
    # 1. 环境变量 OPENAI_API_BASE
    env_base = (os.environ.get("OPENAI_API_BASE") or "").strip()
    if env_base.startswith(("http://", "https://")):
        if logger:
            try:
                logger.warning(f"[llm_adapter] api_base 非法 ({api_base!r}), fallback to env OPENAI_API_BASE={env_base!r}")
            except Exception:
                pass
        return env_base.rstrip("/")
    ds_env = (os.environ.get("DASHSCOPE_API_BASE") or "").strip()
    # 2. 模型名启发 — qwen* / dashscope* → 通义千问 OpenAI 兼容
    name_lower = (model_name or "").lower()
    if name_lower.startswith(("qwen", "dashscope", "qwq", "qvq")):
        target = ds_env if ds_env.startswith(("http://", "https://")) else \
                 "https://dashscope.aliyuncs.com/compatible-mode/v1"
        if logger:
            try:
                logger.warning(f"[llm_adapter] api_base 非法 ({api_base!r}), 启发 model={model_name} → {target!r}")
            except Exception:
                pass
        return target.rstrip("/")
    # 3. 硬编码 OpenAI 默认
    fallback = "https://api.openai.com/v1"
    if logger:
        try:
            logger.warning(f"[llm_adapter] api_base 非法 ({api_base!r}), 用默认 {fallback!r}")
        except Exception:
            pass
    return fallback


def _is_url_clean(url: str) -> bool:
    """检测 URL 是不是干净的 http(s):// + 真实 host."""
    if not isinstance(url, str):
        return False
    low = url.lower().strip()
    if not (low.startswith("http://") or low.startswith("https://")):
        return False
    bad_hosts = ("//undefined", "//null", "//none", "//nan", "://undefined:", "://null:")
    return not any(b in low for b in bad_hosts)


def _assert_clean_url(url: str) -> None:
    """Task XXXX-19 续: 请求前再过一道. adapter 即使被脏值污染了, 这里 fail-fast,
    不让 requests 用 15s timeout 慢慢 host=undefined.

    注意: _BaseHTTPAdapterMixin._heal_url_or_die 会先尝试修, 都失败才到这里.
    """
    if _is_url_clean(url):
        return
    if not isinstance(url, str):
        raise ValueError(f"URL 非字符串: {type(url).__name__}")
    raise ValueError(
        f"URL 含非法 host / 缺 scheme: {url!r}. "
        f"模型 api_base 是脏字符串, 去管理→模型管理重建该 model, "
        f"或检查 .env 里 *_API_BASE 配置."
    )


class _BaseHTTPAdapterMixin:
    """给需要HTTP调用的适配器提供通用requests能力（超时、重试 + SSE 流式）"""

    def _heal_url_or_die(self, url: str) -> str:
        """Task XXXX-19 续续: 即使 self.api_base 在内存里被污染过 (服务没重启,
        旧 adapter 对象拿着脏字符串), 这里运行期再 sanitize 一次, 自动修自身.

        策略:
          1. URL 干净 → 原样返回 (fast path)
          2. URL 脏 → 从 self.api_base 拿出 host 部分判脏, sanitize 之, 把 self.api_base
             也替换掉 (下次调用直接干净, 不用再 heal). 然后拼新 URL 返回.
          3. 仍脏 → _assert_clean_url raise 清晰错误.
        """
        if _is_url_clean(url):
            return url
        # 尝试自愈: 从 self.api_base 重新 sanitize, 重新拼 URL
        try:
            old_base = getattr(self, "api_base", "")
            # adapter_kind: 根据类名启发. _sanitize_api_base 接受 anthropic/ollama/openai.
            cls_name = type(self).__name__.lower()
            if "anthropic" in cls_name:
                kind = "anthropic"
            elif "ollama" in cls_name:
                kind = "ollama"
            else:
                kind = "openai"
            model_name = getattr(self, "model_name", "")
            logger = getattr(self, "logger", None)
            new_base = _sanitize_api_base(
                old_base, model_name=model_name, logger=logger, adapter_kind=kind
            )
            if new_base and new_base != old_base:
                self.api_base = new_base  # mutate 自己, 下次直接干净
                # 重拼 URL: 把旧 base 替换成新 base
                if old_base and old_base in url:
                    healed = url.replace(old_base, new_base, 1)
                else:
                    # 拼 url 时如果没用 self.api_base (理论不会), 兜底直接构造
                    suffix = url.split("/", 3)[-1] if url.count("/") >= 3 else ""
                    healed = new_base.rstrip("/") + "/" + suffix.lstrip("/")
                if _is_url_clean(healed):
                    if logger:
                        try:
                            logger.warning(
                                f"[llm_adapter] runtime heal: {url!r} → {healed!r} "
                                f"(self.api_base 修为 {new_base!r})"
                            )
                        except Exception:
                            pass
                    return healed
        except Exception:
            pass
        # 真没救了, fail-fast
        _assert_clean_url(url)
        return url

    def _post_json(self, url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        url = self._heal_url_or_die(url)
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
        url = self._heal_url_or_die(url)
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
        url = self._heal_url_or_die(url)
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
        url = self._heal_url_or_die(url)
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
