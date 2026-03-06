"""数据层-大模型对接模块（llm_adapter_module）

对外暴露：
- LLMService：统一大模型调用入口
- 数据模型：LLMRequest / LLMResponse / FileContent / MediaContent 等
"""

from .core.impl import LLMService
from .model.data_model import (
    MediaContent,
    FileContent,
    LLMParam,
    LLMRequest,
    MultimodalResult,
    LLMResponse,
)

__all__ = [
    "LLMService",
    "MediaContent",
    "FileContent",
    "LLMParam",
    "LLMRequest",
    "MultimodalResult",
    "LLMResponse",
]
