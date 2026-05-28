# -*- coding: utf-8 -*-
"""
adapters/ — 每 LLM 供应商一个文件 (Task NN #74)

把原 impl.py 1261 行单文件里 5 个 adapter + 1 个 mixin 拆成各自文件:
    _http_mixin.py        _BaseHTTPAdapterMixin   (POST + 3 家 SSE/NDJSON 流式)
    openai_vector.py      OpenAIVectorAdapter
    openai_chat.py        OpenAIChatAdapter       (含 chat_stream)
    openai_multimodal.py  OpenAIMultimodalAdapter
    anthropic_chat.py     AnthropicChatAdapter    (含 chat_stream)
    ollama_chat.py        OllamaChatAdapter       (含 chat_stream)

加新供应商: 新建 <vendor>_<kind>.py + 在 LLMService.init_adapters 里 dispatch.
LLMService 自身留在 core/impl.py.
"""

from ._http_mixin import _BaseHTTPAdapterMixin
from .openai_vector import OpenAIVectorAdapter
from .openai_chat import OpenAIChatAdapter
from .openai_multimodal import OpenAIMultimodalAdapter
from .anthropic_chat import AnthropicChatAdapter
from .ollama_chat import OllamaChatAdapter

__all__ = [
    "_BaseHTTPAdapterMixin",
    "OpenAIVectorAdapter",
    "OpenAIChatAdapter",
    "OpenAIMultimodalAdapter",
    "AnthropicChatAdapter",
    "OllamaChatAdapter",
]
