"""llm_adapter_module package.

Expose core service + abstract base for external modules.
"""
from .core.base import BaseLLMService
from .core.impl import LLMService
from .model.data_model import (
    FileContent, MediaContent,
    LLMRequest, LLMResponse, LLMParam, MultimodalResult,
)

__all__ = [
    "BaseLLMService",
    "LLMService",
    "FileContent", "MediaContent",
    "LLMRequest", "LLMResponse", "LLMParam", "MultimodalResult",
]
