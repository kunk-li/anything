# -*- coding: utf-8 -*-
"""
协同调度模块统一数据模型定义
遵循系统整体数据规范，使用 dataclass 定义请求/响应结构
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class OrchestratorRequest:
    """协同调度统一请求模型"""
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


@dataclass
class OrchestratorResponse:
    """协同调度统一响应模型，遵循系统统一异常码规范"""
    # 响应码：SUCCESS 或系统异常码
    code: str
    # 响应信息
    message: str
    # 响应数据（包含路由类型与底层模块结果）
    data: Optional[Dict[str, Any]] = None
    # 实际路由类型（rag/agent/hybrid）
    route_type: Optional[str] = None
    # 调用耗时（秒，可选）
    cost_time: Optional[float] = None
    # 链路追踪 ID（可选）
    trace_id: Optional[str] = None