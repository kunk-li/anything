# -*- coding: utf-8 -*-
"""
请求响应处理模块专属工具函数
提供请求校验、响应封装、异常转换等辅助函数
"""

from typing import Dict, Any, Tuple, Optional


def validate_request_params(request: Dict[str, Any]) -> Tuple[bool, str]:
    """
    校验请求参数完整性
    :param request: 请求字典
    :return: 元组（校验是否通过，错误信息）
    """
    req_type = request.get("type", "rag")

    # 1. 检查 type 是否合法
    if req_type not in ["rag", "agent", "hybrid"]:
        return False, f"不支持的请求类型：{req_type}"

    # 2. 检查 rag 模式下 query 是否存在
    if req_type == "rag" and not request.get("query"):
        return False, "RAG 模式必须提供 query 参数"

    # 3. 检查 agent 模式下 task 是否存在
    if req_type in ["agent", "hybrid"] and not request.get("task"):
        return False, "Agent 模式必须提供 task 参数"

    # 4. 检查 top_k 范围
    top_k = request.get("top_k", 5)
    if not isinstance(top_k, int) or top_k < 1 or top_k > 50:
        return False, "top_k 参数必须为 1-50 之间的整数"

    return True, ""


def build_error_details(code: str, request: Dict[str, Any]) -> Optional[Dict]:
    """
    构建错误详情信息，用于响应中的 details 字段
    :param code: 错误码
    :param request: 原始请求
    :return: 错误详情字典（失败时）或 None（成功时）
    """
    if code == "SUCCESS":
        return None

    # 根据错误码生成对应的 details 结构
    details_map = {
        "PARAM_MISSING": {
            "field": "query/task",
            "expected": "string",
            "example": "用户问题内容/任务描述"
        },
        "PARAM_INVALID": {
            "field": "top_k",
            "expected": "integer (1~50)",
            "actual": request.get("top_k"),
            "example": 10
        },
        "BAD_REQUEST": {
            "field": "type",
            "allowed": ["rag", "agent", "hybrid"],
            "example": "rag"
        },
        "REQUEST_TOO_LARGE": {
            "hint": "请减小请求大小或分批次发送"
        },
        "REQUEST_TIMEOUT": {
            "timeout_ms": 60000,
            "stage": "request_handle",
            "hint": "建议拆分任务或降低单次输入规模"
        }
    }

    return details_map.get(code, {"hint": "请查看日志排查具体原因"})


def generate_trace_id() -> str:
    """
    生成链路追踪 ID
    :return: 追踪 ID 字符串
    """
    import uuid
    return uuid.uuid4().hex