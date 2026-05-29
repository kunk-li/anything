# -*- coding: utf-8 -*-
"""
SessionsRoutesMixin (Task SSS #105)

3 个会话管理路由 (基于 state_store_module):
    GET    /sessions/list           列已知 session_id
    DELETE /sessions/{session_id}   清除 session 状态
    POST   /sessions                创建新 session (返回新 uuid)

self.state_store=None 时返 SERVICE_UNAVAILABLE 501.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import JSONResponse


class SessionsRoutesMixin:
    """会话管理路由 mixin."""

    def _register_sessions_routes(self) -> None:
        @self.app.get("/sessions/list")
        async def sessions_list(request: Request):
            trace_id = request.state.trace_id
            if not getattr(self, "state_store", None):
                return JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "state_store 未注入",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=501,
                )
            try:
                limit = int(request.query_params.get("limit", "50"))
            except ValueError:
                limit = 50
            try:
                sessions = self.state_store.list_sessions(limit=limit)
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": {"count": len(sessions), "sessions": sessions},
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "SESSIONS_LIST_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=500,
                )

        @self.app.delete("/sessions/{session_id}")
        async def sessions_delete(session_id: str, request: Request):
            trace_id = request.state.trace_id
            if not getattr(self, "state_store", None):
                return JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "state_store 未注入",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=501,
                )
            try:
                ok = self.state_store.clear_state(session_id)
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": {"deleted": True, "session_id": session_id, "result": ok},
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "SESSIONS_DELETE_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=500,
                )

        @self.app.post("/sessions")
        async def sessions_create(request: Request):
            """创建新 session: 返回 uuid (state_store 第一次 save 时才落盘)."""
            trace_id = request.state.trace_id
            new_id = "sess_" + uuid.uuid4().hex[:12]
            return JSONResponse({
                "code": "SUCCESS", "message": "ok",
                "data": {"session_id": new_id},
                "trace_id": trace_id, "retryable": False, "details": None,
            })
