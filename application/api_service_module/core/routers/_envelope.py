# -*- coding: utf-8 -*-
"""
共用响应封装 envelope() —— 把各路由里重复手写的统一响应体收成一处。

统一响应体 (全仓约定): {code, message, data, trace_id, retryable, details}。
此前 13 个 router 各自内联手写这坨 dict (100+ 处), 改一个字段要扫一圈。收成一个
behavior-preserving 的工厂: 每个调用点显式传原有 status_code / retryable / details /
是否带 X-Request-Id 头, 保证逐处行为不变 (机器迁移, 见同提交的 codemod)。

request_id=True 时附 X-Request-Id 头 (原本带 headers={"X-Request-Id": trace_id} 的端点);
False 时不附 (原本不带头的端点) —— 两类端点本就不同, 不强行统一。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi.responses import JSONResponse


def envelope(
    trace_id: Optional[str],
    *,
    code: str = "SUCCESS",
    message: str = "ok",
    data: Any = None,
    status_code: int = 200,
    retryable: bool = False,
    details: Any = None,
    request_id: bool = False,
) -> JSONResponse:
    """构造统一响应体 JSONResponse。字段语义与各 router 原内联 dict 完全一致。"""
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "data": data,
            "trace_id": trace_id,
            "retryable": retryable,
            "details": details,
        },
        headers={"X-Request-Id": trace_id} if request_id else None,
    )
