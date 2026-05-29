# -*- coding: utf-8 -*-
"""
MemoryRoutesMixin (Task GGG #93)

5 个长期记忆管理路由:
    GET    /memory/list          列 tenant 下所有 fact (分页 + tag filter)
    GET    /memory/{fact_id}     单条 fact 详情
    DELETE /memory/{fact_id}     删除 fact (硬删, 不可恢复)
    POST   /memory/{fact_id}/pin pin/unpin fact (pinned 不被 prune)
    POST   /memory/search        Agent 视角的 search_facts 调试用

权限: 跟其他 admin 路由一样默认走 X-API-Key 鉴权. tenant_id 从 auth context
解析 (跟 /documents/* 一致), 防止 tenant A 看见 tenant B 的 fact.

注: 这些路由也会被 VV (#82) 的 _register_v1_aliases 自动加 /v1/memory/* 镜像.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse


class MemoryRoutesMixin:
    """长期记忆管理路由 mixin."""

    def _register_memory_routes(self) -> None:
        @self.app.get("/memory/list")
        async def memory_list(request: Request):
            trace_id = request.state.trace_id
            if self.long_term_memory is None:
                return JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "long_term_memory 未注入",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=501,
                )
            tenant = self._memory_tenant_from_request(request)
            try:
                limit = int(request.query_params.get("limit", "50"))
                offset = int(request.query_params.get("offset", "0"))
            except ValueError:
                limit, offset = 50, 0
            tags_filter_raw = request.query_params.get("tags") or ""
            tags_filter = [t.strip() for t in tags_filter_raw.split(",") if t.strip()] or None

            try:
                facts = self.long_term_memory.list_facts(
                    tenant_id=tenant, limit=limit, offset=offset,
                    tags_filter=tags_filter,
                )
                items: List[Dict[str, Any]] = []
                for f in facts:
                    items.append({
                        "fact_id": f.fact_id,
                        "content": f.content,
                        "tags": f.tags,
                        "confidence": f.confidence,
                        "pinned": f.pinned,
                        "access_count": f.access_count,
                        "created_at": f.created_at,
                        "last_accessed": f.last_accessed,
                        "session_id": f.session_id,
                    })
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": {
                        "tenant_id": tenant, "count": len(items),
                        "limit": limit, "offset": offset, "facts": items,
                    },
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                self.logger.error(f"[memory] list 失败 trace_id={trace_id}: {e}")
                return JSONResponse(
                    {"code": "MEMORY_LIST_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=500,
                )

        @self.app.get("/memory/{fact_id}")
        async def memory_get(fact_id: str, request: Request):
            trace_id = request.state.trace_id
            if self.long_term_memory is None:
                return JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "long_term_memory 未注入",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=501,
                )
            tenant = self._memory_tenant_from_request(request)
            try:
                f = self.long_term_memory._load_fact(tenant, fact_id)
                if f is None:
                    return JSONResponse(
                        {"code": "MEMORY_NOT_FOUND", "message": f"fact {fact_id} 不存在",
                         "data": None, "trace_id": trace_id,
                         "retryable": False, "details": None},
                        status_code=404,
                    )
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": f.model_dump(mode="json"),
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "MEMORY_GET_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=500,
                )

        @self.app.delete("/memory/{fact_id}")
        async def memory_delete(fact_id: str, request: Request):
            trace_id = request.state.trace_id
            if self.long_term_memory is None:
                return JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "long_term_memory 未注入",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=501,
                )
            tenant = self._memory_tenant_from_request(request)
            try:
                ok = self.long_term_memory.delete_fact_in_tenant(fact_id, tenant)
                if not ok:
                    return JSONResponse(
                        {"code": "MEMORY_NOT_FOUND", "message": f"fact {fact_id} 不存在",
                         "data": None, "trace_id": trace_id,
                         "retryable": False, "details": None},
                        status_code=404,
                    )
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": {"deleted": True, "fact_id": fact_id},
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "MEMORY_DELETE_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=500,
                )

        @self.app.post("/memory/{fact_id}/pin")
        async def memory_pin(fact_id: str, request: Request):
            trace_id = request.state.trace_id
            if self.long_term_memory is None:
                return JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "long_term_memory 未注入",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=501,
                )
            tenant = self._memory_tenant_from_request(request)
            try:
                body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
            except Exception:
                body = {}
            new_pin = bool(body.get("pinned", True))
            try:
                f = self.long_term_memory._load_fact(tenant, fact_id)
                if f is None:
                    return JSONResponse(
                        {"code": "MEMORY_NOT_FOUND", "message": f"fact {fact_id} 不存在",
                         "data": None, "trace_id": trace_id,
                         "retryable": False, "details": None},
                        status_code=404,
                    )
                f.pinned = new_pin
                self.long_term_memory._save_fact(f)
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": {"fact_id": fact_id, "pinned": f.pinned},
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "MEMORY_PIN_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=500,
                )

        @self.app.post("/memory/search")
        async def memory_search(request: Request):
            """Agent 视角的 search_facts, debug 用. body: {query, top_k?, tags_filter?}"""
            trace_id = request.state.trace_id
            if self.long_term_memory is None:
                return JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "long_term_memory 未注入",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=501,
                )
            tenant = self._memory_tenant_from_request(request)
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {"code": "BAD_REQUEST", "message": "body 非合法 JSON",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=400,
                )
            query_str = str(body.get("query") or "").strip()
            if not query_str:
                return JSONResponse(
                    {"code": "PARAM_MISSING", "message": "query 不能为空",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=400,
                )
            try:
                from long_term_memory_module import MemoryQuery
                q = MemoryQuery(
                    query=query_str, tenant_id=tenant,
                    top_k=int(body.get("top_k") or 5),
                    tags_filter=body.get("tags_filter") or None,
                )
                hits = self.long_term_memory.search_facts(q)
                items = [
                    {
                        "fact_id": h.fact.fact_id,
                        "content": h.fact.content,
                        "score": round(h.score, 4),
                        "reason": h.reason,
                        "tags": h.fact.tags,
                    }
                    for h in hits
                ]
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": {
                        "tenant_id": tenant, "query": query_str,
                        "count": len(items), "hits": items,
                    },
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "MEMORY_SEARCH_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=500,
                )

    # ------------------------------------------------------------------
    # helper: tenant 解析 (跟 documents/admin 一致的优先级)
    # ------------------------------------------------------------------

    def _memory_tenant_from_request(self, request: Request) -> str:
        # 1. auth context (从 X-API-Key 反查的)
        tenant = getattr(request.state, "auth_tenant_id", None)
        if tenant:
            return str(tenant)
        # 2. query string
        q_tenant = request.query_params.get("tenant_id")
        if q_tenant:
            return str(q_tenant)
        # 3. observability context (middleware 设的)
        try:
            from observability_module import get_current_tenant
            cur = get_current_tenant()
            if cur:
                return str(cur)
        except Exception:
            pass
        return "default"
