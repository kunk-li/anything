from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Literal

RequestType = Literal["VECTOR", "CHAT", "MULTIMODAL"]
MediaType = Literal["image", "audio", "video"]

@dataclass
class MediaContent:
    """媒体内容子模型（适配多模态）"""
    media_type: MediaType
    media_path: str
    media_base64: Optional[str] = None
    media_metadata: Optional[Dict[str, Any]] = None

@dataclass
class FileContent:
    """文件内容标准化模型（支持多模态），对接文档解析模块"""
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
    request_type: RequestType
    input_text: Optional[str] = None
    batch_input: Optional[List[str]] = None
    file_content: Optional[FileContent] = None
    media_input: Optional[List[MediaContent]] = None
    model_param: LLMParam = field(default_factory=LLMParam)
    model_name: str = "default"
    # 可选：多轮对话上下文。若传入则优先使用 messages。
    messages: Optional[List[Dict[str, Any]]] = None

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
