# -*- coding: utf-8 -*-
"""
Agent 模块统一数据模型定义
遵循系统整体数据规范，使用 dataclass 定义请求/响应结构
"""

from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field


@dataclass
class AgentRequest:
    """Agent 任务执行统一请求模型"""
    # 用户任务描述（必填）
    task: str
    # 会话唯一标识（可选，为空则自动生成）
    session_id: Optional[str] = None
    # 工具调用最大重试次数（可选）
    max_retries: Optional[int] = None
    # 任务执行超时时间（秒，可选）
    timeout: Optional[int] = None
    # 附加参数（可选）
    extra_params: Optional[Dict[str, Any]] = None


@dataclass
class TaskStep:
    """任务子步骤模型"""
    # 工具名称（如 rag_search, calculator）
    tool: str
    # 工具输入参数
    input: Dict[str, Any]
    # 步骤描述（可选）
    description: Optional[str] = None


@dataclass
class TaskPlan:
    """任务执行计划模型"""
    # 子步骤列表
    plan: List[TaskStep] = field(default_factory=list)
    # 计划生成时间
    created_at: Optional[str] = None


@dataclass
class ToolResult:
    """工具执行结果模型"""
    # 工具名称
    tool: str
    # 工具输出（标准化响应格式）
    output: Dict[str, Any]


@dataclass
class AgentResponse:
    """Agent 任务执行统一响应模型，遵循系统统一异常码规范"""
    # 响应码：SUCCESS 或系统异常码
    code: str
    # 响应信息
    message: str
    # 响应数据
    data: Optional[Dict[str, Any]] = None
    # 调用耗时（秒，可选）
    cost_time: Optional[float] = None
    # 链路追踪 ID（可选）
    trace_id: Optional[str] = None


@dataclass
class StateEvent:
    """Agent 状态事件模型，用于状态存储模块持久化"""
    # 事件类型（plan/tool/result/error）
    event_type: str
    # 事件数据
    data: Dict[str, Any]
    # 事件时间戳
    timestamp: str