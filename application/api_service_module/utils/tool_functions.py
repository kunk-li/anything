"""API服务模块工具函数。"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from api_service_module.model.data_model import ErrorDetails

VALID_REQUEST_TYPES = {"rag", "agent", "hybrid"}
RETRYABLE_CODES = {
    "API_RATE_LIMITED",
    "ORCHESTRATOR_RUN_FAILED",
    "REQUEST_TIMEOUT",
    "RAG_RUN_FAILED",
    "AGENT_TIMEOUT",
    "TOOL_CALL_FAILED",
    "UNKNOWN_ERROR",
}


def utc_now_iso() -> str:
    """返回 UTC ISO 时间。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def generate_trace_id() -> str:
    """生成链路追踪 ID。"""
    return uuid.uuid4().hex


def generate_job_id() -> str:
    """生成索引任务 ID。"""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"job_{timestamp}_{uuid.uuid4().hex[:8]}"


def validate_request_params(request: Dict[str, Any]) -> Tuple[bool, str, str]:
    """校验统一业务请求。"""
    if not isinstance(request, dict):
        return False, "请求体必须为 JSON 对象", "PARAM_INVALID"

    request_type = request.get("type", "rag")
    if request_type not in VALID_REQUEST_TYPES:
        return False, f"不支持的type：{request_type}", "BAD_REQUEST"

    if request_type == "rag" and not request.get("query"):
        return False, "缺少必填参数：query", "PARAM_MISSING"

    if request_type in {"agent", "hybrid"} and not request.get("task"):
        return False, "缺少必填参数：task", "PARAM_MISSING"

    if "top_k" in request:
        top_k = request.get("top_k")
        if not isinstance(top_k, int):
            return False, "参数不合法：top_k 必须为整数", "PARAM_INVALID"
        if not 1 <= top_k <= 50:
            return False, "参数不合法：top_k 取值范围为 1~50", "PARAM_INVALID"

    session_id = request.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        return False, "参数不合法：session_id 必须为字符串", "PARAM_INVALID"

    return True, "", "SUCCESS"


def build_error_details(code: str, request: Optional[Dict[str, Any]] = None, message: str = "") -> Optional[Dict[str, Any]]:
    """为错误码生成结构化 details。"""
    request = request or {}
    if code == "PARAM_MISSING":
        field_name = "query" if "query" in message else "task"
        return ErrorDetails(
            field=field_name,
            expected="string",
            actual=None,
            example="问题内容" if field_name == "query" else "请整理一份开发计划",
            hint=f"请在请求体中补充 {field_name} 字段",
        ).to_dict()

    if code == "PARAM_INVALID":
        if "top_k" in message:
            return ErrorDetails(
                field="top_k",
                expected="integer (1~50)",
                actual=type(request.get("top_k")).__name__ if "top_k" in request else None,
                example=5,
                hint="请传入 1 到 50 之间的整数",
            ).to_dict()
        return ErrorDetails(
            expected="合法请求参数",
            actual=request,
            hint="请检查字段类型与取值范围",
        ).to_dict()

    if code == "BAD_REQUEST":
        return ErrorDetails(
            field="type",
            expected=["rag", "agent", "hybrid"],
            actual=request.get("type"),
            example="rag",
            hint="请传入受支持的请求类型",
        ).to_dict()

    if code == "REQUEST_TOO_LARGE":
        return ErrorDetails(
            field="body",
            expected="请求大小不得超过限制",
            actual=request.get("content_length"),
            hint="请缩小请求体或采用文件上传方式",
        ).to_dict()

    if code == "AUTH_REQUIRED":
        return ErrorDetails(
            field="X-API-Key",
            expected="有效 API Key",
            hint="请在请求头中携带 X-API-Key",
        ).to_dict()

    if code == "FOLDER_NOT_FOUND":
        return ErrorDetails(
            field="source_path",
            expected="存在的目录路径",
            actual=request.get("source_path"),
            hint="请确认目录存在并具有读取权限",
        ).to_dict()

    if code == "UNKNOWN_ERROR":
        return ErrorDetails(hint="请结合 trace_id 排查服务端日志").to_dict()

    return None


def should_retry(code: str) -> bool:
    """根据错误码判断是否可重试。"""
    return code in RETRYABLE_CODES


def standardize_request(request: Dict[str, Any], default_type: str = "rag", default_top_k: int = 5) -> Dict[str, Any]:
    """补齐统一请求默认值。"""
    normalized = dict(request)
    normalized.setdefault("type", default_type)
    normalized.setdefault("top_k", default_top_k)
    normalized.setdefault("extra_params", {})
    normalized.setdefault("session_id", uuid.uuid4().hex)
    normalized.setdefault("timestamp", utc_now_iso())
    return normalized


def ensure_directory(path: str) -> Path:
    """确保目录存在。"""
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def safe_file_name(file_name: str) -> str:
    """简易文件名清洗。"""
    return os.path.basename(file_name).replace("..", "_")
