# -*- coding: utf-8 -*-
"""
协同调度模块专属工具函数
提供调度相关的辅助函数
"""

from typing import Dict, Any
from exception_module.core.impl import OrchestratorException


def validate_request_params(request: Dict[str, Any]) -> bool:
    """
    校验请求参数完整性
    :param request: 请求字典
    :return: 校验通过返回 True，否则抛出异常
    """
    req_type = request.get("type", "rag")

    # 1. 检查 type 是否合法
    if req_type not in ["rag", "agent", "hybrid"]:
        raise OrchestratorException(
            "BAD_REQUEST",
            f"不支持的请求类型：{req_type}"
        )

    # 2. 检查 rag 模式下 query 是否存在
    if req_type == "rag" and not request.get("query"):
        raise OrchestratorException(
            "PARAM_MISSING",
            "RAG 模式必须提供 query 参数"
        )

    # 3. 检查 agent 模式下 task 是否存在
    if req_type in ["agent", "hybrid"] and not request.get("task"):
        raise OrchestratorException(
            "PARAM_MISSING",
            "Agent 模式必须提供 task 参数"
        )

    return True


def wrap_response_data(route_type: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """
    封装响应数据，添加路由类型标识
    :param route_type: 路由类型
    :param result: 底层模块返回结果
    :return: 封装后的数据字典
    """
    return {
        "route_type": route_type,
        "result": result
    }