"""llm_adapter_module package.

Expose core service for external modules.
"""
from .core.impl import LLMService
from .model.data_model import (
    FileContent, MediaContent,
    LLMRequest, LLMResponse, LLMParam, MultimodalResult,
)

__all__ = [
    "LLMService",
    "FileContent", "MediaContent",
    "LLMRequest", "LLMResponse", "LLMParam", "MultimodalResult",
]
