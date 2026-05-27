# -*- coding: utf-8 -*-
"""
系统统一 Schema 定义（Pydantic v2）

依据：
    - 文档第 8 章：统一请求格式
    - 文档第 10 章：统一响应信封 + 错误码

设计原则：
    - schema 仅在系统边界(API / 接口层入口、对外响应)做强校验
    - 内部模块仍可传 dict，避免每个函数都付出 model 序列化开销
    - 通过 model_validate / model_dump 与 dict 互转
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


# 请求类型枚举
RequestType = Literal["rag", "agent", "hybrid"]

# tenant_id 字符集白名单(详见 docs/multi-tenancy-design.md §2.1 / §9.2)
# 严格 ASCII 防 path traversal; 长度 3-32
TENANT_ID_PATTERN = re.compile(r"^[a-z0-9_-]{3,32}$")


class RequestEnvelope(BaseModel):
    """系统统一请求 schema（对应文档第 8 章）。

    业务规则：
        - type=rag → query 必填
        - type=agent / hybrid → task 必填
        - top_k: 1~50 闭区间
        - session_id / trace_id 可选（缺失时由接口层补齐）
        - tenant_id 可选,字符集 [a-z0-9_-] 3-32 字符
          (缺失时由 RequestHandler 补齐为 "default", 详见 docs/multi-tenancy-design.md §4)
        - extra_params: 透传字段，不允许中途改名
    """

    type: RequestType = "rag"
    query: Optional[str] = None
    task: Optional[str] = None
    session_id: Optional[str] = None
    tenant_id: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=50)
    trace_id: Optional[str] = None
    extra_params: Dict[str, Any] = Field(default_factory=dict)

    # 允许接收 dict 中多余字段（向前兼容），但内部不会保留它们
    model_config = {"extra": "ignore"}

    @field_validator("query", "task", mode="before")
    @classmethod
    def _empty_string_to_none(cls, v: Any) -> Any:
        """空字符串视作未提供，统一为 None 便于后续 required 校验。"""
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @field_validator("tenant_id", mode="before")
    @classmethod
    def _normalize_tenant_id(cls, v: Any) -> Any:
        """空字符串视为未提供 (跟 query/task 一样的清洗);
        非空时严格校验字符集,防 path traversal。"""
        if v is None:
            return None
        if isinstance(v, str) and v.strip() == "":
            return None
        if not isinstance(v, str) or not TENANT_ID_PATTERN.match(v):
            raise ValueError(
                f"tenant_id 必须是 3-32 位 [a-z0-9_-] 字符,实际收到: {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _check_required_per_type(self) -> "RequestEnvelope":
        if self.type == "rag" and not self.query:
            raise ValueError("RAG 模式必须提供 query 参数")
        if self.type in ("agent", "hybrid") and not self.task:
            raise ValueError("Agent 模式必须提供 task 参数")
        return self


class ResponseEnvelope(BaseModel):
    """系统统一响应信封（对应文档第 10 章）。

    任何对外返回必须符合此结构。内部计算保留 dict 风格无需强制使用本 schema。
    """

    code: str = "SUCCESS"
    message: str = "ok"
    data: Optional[Any] = None
    trace_id: Optional[str] = None
    retryable: bool = False
    details: Optional[Dict[str, Any]] = None
    cost_time: Optional[float] = None

    model_config = {"extra": "ignore"}


# ===========================================================================
# 内部高频流转的数据 schema
# ===========================================================================


class ChunkMeta(BaseModel):
    """单个 chunk 的元信息(文档第 11.1 节强制字段)。"""

    file_name: str
    source: str = "local"
    chunk_index: int = Field(ge=0)
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    token_count_est: int = Field(ge=0)

    model_config = {"extra": "allow"}  # 允许扩展字段(table_id/row_range 等)


class Chunk(BaseModel):
    """chunker.chunk_document 输出的标准 chunk 结构(文档第 11.1 节)。

    强制契约:
        chunk_id 推荐格式 "{doc_id}#c000001" (6 位零填充)
        meta 必须包含 file_name/source/chunk_index/start_char/end_char/token_count_est
    """

    doc_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    meta: ChunkMeta

    model_config = {"extra": "ignore"}


class RetrievedChunk(BaseModel):
    """RAG.retrieve 返回的 chunk 级检索结果。

    跟 Chunk 不同之处:
        - 包含相似度 score (0~1)
        - content 可选(由 doc_store 回查时可能未填)
        - file_name / chunk_index 提到顶层,便于上游不解开 meta 也能用
    """

    chunk_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    file_name: Optional[str] = None
    chunk_index: Optional[int] = Field(default=None, ge=0)
    score: float = Field(ge=0.0, le=1.0)
    content: Optional[str] = None
    start_char: Optional[int] = Field(default=None, ge=0)
    end_char: Optional[int] = Field(default=None, ge=0)

    model_config = {"extra": "ignore"}


class Citation(BaseModel):
    """RAG 答案引用项。

    用于把答案中的关键结论绑定到具体 chunk,支持调用方做"点击跳转到原文"。
    """

    chunk_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    file_name: Optional[str] = None
    start_char: Optional[int] = Field(default=None, ge=0)
    end_char: Optional[int] = Field(default=None, ge=0)
    score: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    model_config = {"extra": "ignore"}


class ToolStep(BaseModel):
    """Agent 执行计划中的单个步骤。"""

    step_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    description: str = ""
    input_data: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


def validate_request_dict(request: Dict[str, Any]) -> Tuple[bool, str, str]:
    """将 dict 请求按 RequestEnvelope 校验，返回 (is_valid, message, error_code)。

    error_code 直接对齐文档错误码表：
        - SUCCESS / BAD_REQUEST / PARAM_MISSING / PARAM_INVALID
    """
    try:
        RequestEnvelope.model_validate(request)
        return True, "", "SUCCESS"
    except ValidationError as e:
        errors = e.errors()
        if not errors:
            return False, "请求校验失败", "PARAM_INVALID"
        first = errors[0]
        loc = first.get("loc", ())
        err_type = first.get("type", "")
        msg = first.get("msg", "请求参数不合法")

        # 错误信息中可能包含 "Value error, " 前缀，做一次清洗
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]

        # 根据出错位置/类型映射业务错误码
        if loc and loc[0] == "type":
            return False, msg, "BAD_REQUEST"
        if loc and loc[0] == "top_k":
            return False, msg, "PARAM_INVALID"
        if loc and loc[0] == "tenant_id":
            # tenant_id 字符集校验失败属于参数不合法,而非缺失
            return False, msg, "PARAM_INVALID"
        # type-level required（model_validator 抛出的业务规则错误）
        if err_type in ("value_error",) or "必须提供" in msg:
            return False, msg, "PARAM_MISSING"
        return False, msg, "PARAM_INVALID"
