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
from ._envelope import envelope


class MemoryRoutesMixin:
    """长期记忆管理路由 mixin."""

    def _register_memory_routes(self) -> None:
        @self.app.get("/memory/list")
        async def memory_list(request: Request):
            trace_id = request.state.trace_id
            if self.long_term_memory is None:
                return envelope(trace_id, code="SERVICE_UNAVAILABLE", message="long_term_memory 未注入", status_code=501)
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
                return envelope(trace_id, data={
                        "tenant_id": tenant, "count": len(items),
                        "limit": limit, "offset": offset, "facts": items,
                    })
            except Exception as e:
                self.logger.error(f"[memory] list 失败 trace_id={trace_id}: {e}")
                return envelope(trace_id, code="MEMORY_LIST_FAILED", message=str(e), status_code=500)

        # 执行计划⑥ (可见性面板): 用户画像 5 维度 — 把"Agent 眼中的你"从黑盒变可见。
        # 注: 必须注册在 /memory/{fact_id} 之前, 否则 "profile" 会被当 fact_id 捕获。
        @self.app.get("/memory/profile")
        async def memory_profile(request: Request):
            trace_id = request.state.trace_id
            if self.long_term_memory is None:
                return envelope(trace_id, code="SERVICE_UNAVAILABLE", message="long_term_memory 未注入", status_code=501)
            tenant = self._memory_tenant_from_request(request)
            try:
                profile = self.long_term_memory.get_user_profile(tenant) or {}
                dims = {str(k): list(v) for k, v in profile.items()}
                total = sum(len(v) for v in dims.values())
                return envelope(trace_id, data={"tenant_id": tenant, "total": total, "profile": dims})
            except Exception as e:
                self.logger.error(f"[memory] profile 失败 trace_id={trace_id}: {e}")
                return envelope(trace_id, code="MEMORY_PROFILE_FAILED", message=str(e), status_code=500)

        @self.app.get("/memory/{fact_id}")
        async def memory_get(fact_id: str, request: Request):
            trace_id = request.state.trace_id
            if self.long_term_memory is None:
                return envelope(trace_id, code="SERVICE_UNAVAILABLE", message="long_term_memory 未注入", status_code=501)
            tenant = self._memory_tenant_from_request(request)
            try:
                f = self.long_term_memory._load_fact(tenant, fact_id)
                if f is None:
                    return envelope(trace_id, code="MEMORY_NOT_FOUND", message=f"fact {fact_id} 不存在", status_code=404)
                return envelope(trace_id, data=f.model_dump(mode="json"))
            except Exception as e:
                return envelope(trace_id, code="MEMORY_GET_FAILED", message=str(e), status_code=500)

        @self.app.delete("/memory/{fact_id}")
        async def memory_delete(fact_id: str, request: Request):
            trace_id = request.state.trace_id
            if self.long_term_memory is None:
                return envelope(trace_id, code="SERVICE_UNAVAILABLE", message="long_term_memory 未注入", status_code=501)
            tenant = self._memory_tenant_from_request(request)
            try:
                ok = self.long_term_memory.delete_fact_in_tenant(fact_id, tenant)
                if not ok:
                    return envelope(trace_id, code="MEMORY_NOT_FOUND", message=f"fact {fact_id} 不存在", status_code=404)
                return envelope(trace_id, data={"deleted": True, "fact_id": fact_id})
            except Exception as e:
                return envelope(trace_id, code="MEMORY_DELETE_FAILED", message=str(e), status_code=500)

        @self.app.post("/memory/{fact_id}/pin")
        async def memory_pin(fact_id: str, request: Request):
            trace_id = request.state.trace_id
            if self.long_term_memory is None:
                return envelope(trace_id, code="SERVICE_UNAVAILABLE", message="long_term_memory 未注入", status_code=501)
            tenant = self._memory_tenant_from_request(request)
            try:
                body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
            except Exception:
                body = {}
            new_pin = bool(body.get("pinned", True))
            try:
                f = self.long_term_memory._load_fact(tenant, fact_id)
                if f is None:
                    return envelope(trace_id, code="MEMORY_NOT_FOUND", message=f"fact {fact_id} 不存在", status_code=404)
                f.pinned = new_pin
                self.long_term_memory._save_fact(f)
                return envelope(trace_id, data={"fact_id": fact_id, "pinned": f.pinned})
            except Exception as e:
                return envelope(trace_id, code="MEMORY_PIN_FAILED", message=str(e), status_code=500)

        @self.app.post("/memory/search")
        async def memory_search(request: Request):
            """Agent 视角的 search_facts, debug 用. body: {query, top_k?, tags_filter?}"""
            trace_id = request.state.trace_id
            if self.long_term_memory is None:
                return envelope(trace_id, code="SERVICE_UNAVAILABLE", message="long_term_memory 未注入", status_code=501)
            tenant = self._memory_tenant_from_request(request)
            try:
                body = await request.json()
            except Exception:
                return envelope(trace_id, code="BAD_REQUEST", message="body 非合法 JSON", status_code=400)
            query_str = str(body.get("query") or "").strip()
            if not query_str:
                return envelope(trace_id, code="PARAM_MISSING", message="query 不能为空", status_code=400)
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
                return envelope(trace_id, data={
                        "tenant_id": tenant, "query": query_str,
                        "count": len(items), "hits": items,
                    })
            except Exception as e:
                return envelope(trace_id, code="MEMORY_SEARCH_FAILED", message=str(e), status_code=500)

    # ------------------------------------------------------------------
    # helper: tenant 解析 (跟 documents/admin 一致的优先级)
    # ------------------------------------------------------------------

    def _memory_tenant_from_request(self, request: Request) -> str:
        # 1. 认证产物优先 (从 X-API-Key/JWT 反查的 tenant)。
        #    旧代码读 request.state.auth_tenant_id —— 这个属性全仓没有任何地方 set, 故永远 miss,
        #    落到下面 query string: 持 A 租户 key 的请求只要带 ?tenant_id=B 就能越权读 B 的 fact,
        #    破坏模块承诺的租户隔离。改用与 documents/invoke 一致的 _resolve_tenant_from_auth。
        tenant = self._resolve_tenant_from_auth(request)
        if tenant:
            return str(tenant)
        # 2. query string (无认证的 dev / 单租户场景)
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
