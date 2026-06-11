# -*- coding: utf-8 -*-
"""
SessionsRoutesMixin (Task SSS #105 + ZZZZ #117)

4 个会话管理路由 (基于 state_store_module):
    GET    /sessions/list           列已知 session_id
    GET    /sessions/{session_id}   读取 session state (供切换时拉历史)
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

    def _delete_chat_docs_for_session(self, session_id: str) -> tuple:
        """删除绑定到该会话的聊天附件 (scope=chat): document_store 正文 + uploads/ 原件。

        上传链路 (index_runner) 写的是 default tenant 的 document_store, 这里对应清理。
        清理失败不阻塞会话删除 — 只 WARNING (残留可被 upload-janitor 按保留期兜底)。
        返回 (docs_removed, files_removed)。
        """
        docs_removed = 0
        files_removed = 0
        sid = str(session_id or "").strip()
        if not sid or not getattr(self, "document_store_factory", None):
            return docs_removed, files_removed
        from pathlib import Path
        try:
            store = self.document_store_factory("default")
            upload_root = Path(
                self.config.get_config("api_service.upload_dir", "./uploads")
            ).resolve()
            for d in (store.list_documents() or []):
                if d.get("scope") != "chat" or d.get("session_id") != sid:
                    continue
                stored_path = d.get("stored_path")
                try:
                    if store.delete_document(d.get("doc_id")):
                        docs_removed += 1
                except Exception as e:
                    self.logger.warning(
                        f"[sessions.delete] chat doc 清理失败 doc_id={d.get('doc_id')}: {e}")
                    continue
                # 原件回收 — 只删 upload_dir 直系子文件 (同 DELETE /documents 约束)
                if stored_path:
                    try:
                        p = Path(stored_path).resolve()
                        if p.parent == upload_root and p.is_file():
                            p.unlink()
                            files_removed += 1
                    except Exception as e:
                        self.logger.warning(
                            f"[sessions.delete] chat 原件清理失败 {stored_path}: {e}")
        except Exception as e:
            self.logger.warning(f"[sessions.delete] chat 附件联动清理失败 sid={sid}: {e}")
        return docs_removed, files_removed

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
            cursor = request.query_params.get("cursor") or None
            try:
                # cursor 仅在显式传入时透传 — 兼容不带该参的旧 state_store 实现/测试桩
                if cursor:
                    sessions = self.state_store.list_sessions(limit=limit, cursor=cursor)
                else:
                    sessions = self.state_store.list_sessions(limit=limit)
                # 满页才给 next_cursor (不满页说明已到尾, 返 null 让前端停)
                next_cursor = None
                if sessions and len(sessions) >= limit:
                    last = sessions[-1]
                    next_cursor = f"{last['last_modified']}:{last['session_id']}"
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": {"count": len(sessions), "sessions": sessions,
                             "next_cursor": next_cursor},
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "SESSIONS_LIST_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=500,
                )

        # Task ZZZZ (#117): 读取 session state — 切换会话时拉历史用
        @self.app.get("/sessions/{session_id}")
        async def sessions_get(session_id: str, request: Request):
            trace_id = request.state.trace_id
            if not getattr(self, "state_store", None):
                return JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "state_store 未注入",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=501,
                )
            try:
                state = self.state_store.get_state(session_id)
                if state is None:
                    return JSONResponse(
                        {"code": "SESSION_NOT_FOUND", "message": f"session 不存在: {session_id}",
                         "data": None, "trace_id": trace_id,
                         "retryable": False, "details": None},
                        status_code=404,
                    )
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": state,  # 直接返完整 state, 前端按 events 数组里 role=user/assistant 重建
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "SESSIONS_GET_FAILED", "message": str(e),
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
                # 会话附件联动清理: scope=chat 的文档与 session 绑定 (不在向量库/
                # BM25/kb_doc 里, 只需清 document_store 正文 + uploads/ 原件)
                docs_removed, files_removed = self._delete_chat_docs_for_session(session_id)
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": {"deleted": True, "session_id": session_id, "result": ok,
                             "chat_docs_removed": docs_removed,
                             "chat_files_removed": files_removed},
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
            """创建新 session: 生成 uuid 并立刻在 state_store 写一个 stub state,
            让 /sessions/list 立马能扫到 (否则 UI 创建后看不到)."""
            import time as _time
            trace_id = request.state.trace_id
            new_id = "sess_" + uuid.uuid4().hex[:12]
            # 落盘 stub state — 让 list_sessions 立马能扫到这条记录
            if getattr(self, "state_store", None):
                try:
                    self.state_store.save_state(new_id, {
                        "session_id": new_id,
                        "status": "new",
                        "created_at": _time.time(),
                        "events": [],
                        "history": [],
                    })
                except Exception as e:
                    self.logger.warning(
                        f"[sessions] POST 写 stub state 失败 (仍返 id, 上游可重试): {e}"
                    )
            return JSONResponse({
                "code": "SUCCESS", "message": "ok",
                "data": {"session_id": new_id},
                "trace_id": trace_id, "retryable": False, "details": None,
            })
