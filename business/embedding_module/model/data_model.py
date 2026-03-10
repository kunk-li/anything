from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class EmbeddingRequest:
    """向量生成统一请求模型"""

    # 输入类型：SINGLE（单条文本）、BATCH（批量文本）
    input_type: str
    # 单条输入文本（input_type=SINGLE时必填）
    single_text: Optional[str] = None
    # 批量输入文本列表（input_type=BATCH时必填）
    batch_texts: Optional[List[str]] = None
    # 向量模型名称（默认使用系统配置）
    model_name: Optional[str] = None
    # 是否归一化向量（默认 True）
    normalize: bool = True
    # 批量处理大小（默认使用系统配置）
    batch_size: Optional[int] = None


@dataclass
class EmbeddingResponse:
    """向量生成统一响应模型"""

    # 响应码：SUCCESS（成功）、系统异常码（失败）
    code: str
    # 响应信息：成功为 ok，失败为具体错误信息
    message: str
    # 向量结果：统一使用二维列表；单条场景也包装成二维列表
    vector_result: Optional[List[List[float]]] = None
    # 调用耗时（秒，可选）
    cost_time: Optional[float] = None
    # 链路追踪ID（可选）
    trace_id: Optional[str] = None