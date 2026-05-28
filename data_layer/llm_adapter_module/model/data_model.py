from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class MediaContent:
    """媒体内容子模型（适配多模态）"""
    media_type: str                 # image/audio/video
    media_path: str                 # 本地/云存储路径
    media_base64: Optional[str] = None
    media_metadata: Optional[Dict[str, Any]] = None


@dataclass
class FileContent:
    """文件内容标准化模型（支持多模态）"""
    file_name: str
    file_type: str
    text_content: Optional[str] = None
    split_contents: Optional[List[str]] = None
    media_contents: Optional[List[MediaContent]] = None
    file_size: Optional[int] = None
    parse_time: Optional[str] = None


@dataclass
class LLMParam:
    """大模型通用参数子模型"""
    temperature: float = 0.7
    top_k: int = 40
    max_tokens: int = 2000
    batch_size: int = 32
    normalize: bool = True
    media_process_mode: str = "auto"  # auto/extract/raw
    extra_params: Optional[Dict[str, Any]] = None


@dataclass
class LLMRequest:
    """大模型统一请求模型（支持多模态）"""
    request_type: str                              # VECTOR / CHAT / MULTIMODAL
    input_text: Optional[str] = None
    batch_input: Optional[List[str]] = None
    file_content: Optional[FileContent] = None
    media_input: Optional[List[MediaContent]] = None
    model_param: LLMParam = field(default_factory=LLMParam)
    model_name: str = "default"


@dataclass
class MultimodalResult:
    """多模态模型响应子模型"""
    text_result: Optional[str] = None
    media_result: Optional[Dict[str, Any]] = None
    confidence: Optional[float] = None


@dataclass
class LLMResponse:
    """大模型统一响应模型（支持多模态）"""
    code: str
    message: str
    vector_result: Optional[List[List[float]]] = None
    chat_result: Optional[str] = None
    multimodal_result: Optional[MultimodalResult] = None
    request_info: Optional[Dict[str, Any]] = None
    cost_time: Optional[float] = None
    trace_id: Optional[str] = None
    # Task Y (#59): token 用量 + USD 估值 (adapter 从 API 响应抠出来填进)
    # 没有则为 None — 兼容旧 adapter, 不强制
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
