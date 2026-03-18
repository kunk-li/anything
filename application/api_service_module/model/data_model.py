"""API服务模块数据模型定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class UnifiedRequest:
    """系统统一请求模型。"""

    type: str = "rag"
    query: Optional[str] = None
    task: Optional[str] = None
    session_id: Optional[str] = None
    top_k: int = 5
    extra_params: Dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None
    timestamp: Optional[str] = None


@dataclass
class ErrorDetails:
    """标准错误详情。"""

    field: Optional[str] = None
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    example: Optional[Any] = None
    hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            key: value
            for key, value in {
                "field": self.field,
                "expected": self.expected,
                "actual": self.actual,
                "example": self.example,
                "hint": self.hint,
            }.items()
            if value is not None
        }


@dataclass
class UnifiedResponse:
    """系统统一响应模型。"""

    code: str
    message: str
    data: Optional[Dict[str, Any]]
    trace_id: str
    retryable: bool = False
    details: Optional[Dict[str, Any]] = None
    cost_time: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "data": self.data,
            "trace_id": self.trace_id,
            "retryable": self.retryable,
            "details": self.details,
        }
        if self.cost_time is not None:
            payload["cost_time"] = round(self.cost_time, 6)
        return payload


@dataclass
class IndexBuildRequest:
    """索引构建请求。"""

    source_type: str
    source_path: str
    chunking: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IndexJob:
    """索引任务模型。"""

    job_id: str
    status: str
    source_type: str
    source_path: str
    created_at: str
    updated_at: str
    result: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }
