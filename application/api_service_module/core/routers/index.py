# -*- coding: utf-8 -*-
"""
IndexRoutesMixin (Task LL #72)
POST /index/build         触发索引构建 (协议占位)
GET  /index/job/{job_id}  查任务状态
"""

from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.responses import JSONResponse


class IndexRoutesMixin:
    """索引构建 + 任务查询路由."""

    def _register_index_routes(self) -> None:
        @self.app.post("/index/build")
        async def build_index(request: Request):
            trace_id = request.state.trace_id
            # 当前阶段保留协议占位，不在 API 层直接编排 parser/embedding/vector_db
            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "index build started",
                    "data": {
                        "job_id": f"job_{uuid.uuid4().hex[:12]}"
                    },
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.get("/index/job/{job_id}")
        async def get_index_job(job_id: str, request: Request):
            trace_id = request.state.trace_id
            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "ok",
                    "data": {
                        "job_id": job_id,
                        "status": "PENDING",
                    },
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

