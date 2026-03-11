# -*- coding: utf-8 -*-
"""
请求响应处理模块统一数据模型定义
遵循系统整体数据规范，使用 dataclass 定义请求/响应结构
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass, field


@dataclass
class UnifiedRequest:
    """系统统一请求模型"""
    # 请求类型：rag / agent / hybrid
    type: str
    # 用户问题（rag 模式必填）
    query: Optional[str] = None
    # 用户任务（agent/hybrid 模式必填）
    task: Optional[str] = None
    # 会话唯一标识（可选，为空则自动生成）
    session_id: Optional[str] = None
    # 检索片段数量（rag 模式可选）
    top_k: int = 5
    # 附加参数（可选，传递给底层模块）
    extra_params: Optional[Dict[str, Any]] = None
    # 请求来源标识（可选，用于日志追踪）
    source: Optional[str] = None
    # 请求时间戳（可选，用于性能统计）
    timestamp: Optional[str] = None


@dataclass
class ErrorDetails:
    """错误详情模型，用于失败响应中的 details 字段"""
    # 错误字段名
    field: Optional[str] = None
    # 期望值/类型
    expected: Optional[Any] = None
    # 实际值/类型
    actual: Optional[Any] = None
    # 示例值
    example: Optional[Any] = None
    # 修复建议
    hint: Optional[str] = None


@dataclass
class UnifiedResponse:
    """系统统一响应模型，遵循系统统一异常码规范"""
    # 响应码：SUCCESS 或系统异常码
    code: str
    # 响应信息
    message: str
    # 链路追踪 ID（所有响应必须返回）
    trace_id: str
    # 响应数据
    data: Optional[Dict[str, Any]] = None
    # 是否建议重试
    retryable: bool = False
    # 结构化扩展信息（失败时提供详细信息）
    details: Optional[Dict[str, Any]] = None
    # 调用耗时（秒，可选）
    cost_time: Optional[float] = None
