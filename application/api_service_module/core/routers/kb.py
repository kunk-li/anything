# -*- coding: utf-8 -*-
"""
Knowledge Base routes (Task PM-5)

KB = 一组文档的命名分组. 每个 session 可 attach 1 个 KB, RAG 检索时
只在该 KB 内搜 (而不是全局所有文档).

数据模型:
    kb (id PK, name, description, tenant_id, created_at)
    kb_doc (kb_id FK, doc_id, added_at)  — n:m

端点:
    POST   /kb                    创建 KB
    GET    /kb                    list 当前 tenant 的 KB
    GET    /kb/{id}               含成员 doc_id 列表
    DELETE /kb/{id}               删 KB (不删文档本身)
    POST   /kb/{id}/docs          {doc_ids: [...]} 加成员
    DELETE /kb/{id}/docs/{doc_id} 移除成员

设计:
    - 跟 feedback 一样, sqlite WAL, 加新字段只加 column
    - 不强校验 doc_id 在 document_store 真存在 (跨模块成本高), 让用户自负责
    - RAG 检索的 KB filter 实现在 rag_module, 这里只是元数据存储
"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from ._envelope import envelope


_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    tenant_id TEXT DEFAULT 'default',
    created_at INTEGER NOT NULL,
    created_at_iso TEXT
);
CREATE TABLE IF NOT EXISTS kb_doc (
    kb_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (kb_id, doc_id),
    FOREIGN KEY (kb_id) REFERENCES kb(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_kb_tenant ON kb(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_kbdoc_kb ON kb_doc(kb_id);
CREATE INDEX IF NOT EXISTS idx_kbdoc_doc ON kb_doc(doc_id);
"""


def _get_db_path() -> Path:
    root = Path(os.environ.get("ANYTHING_DATA_ROOT", "run")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / "kb.sqlite3"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_get_db_path()), timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


def _kb_row(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "tenant_id": row[3],
        "created_at_iso": row[5],
    }


class KbRoutesMixin:
    """Knowledge Base router mixin."""

    def _register_kb_routes(self) -> None:

        @self.app.post("/kb")
        async def kb_create(request: Request):
            trace_id = request.state.trace_id
            try:
                body = await request.json()
            except Exception:
                return _bad(trace_id, "请求体非合法 JSON")
            name = (body.get("name") or "").strip()
            if not name:
                return _bad(trace_id, "name 必填")
            description = (body.get("description") or "").strip() or None
            tenant_id = (body.get("tenant_id") or "default").strip() or "default"
            kb_id = "kb_" + uuid.uuid4().hex[:12]
            now = int(time.time())
            iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
            try:
                conn = _get_conn()
                conn.execute(
                    "INSERT INTO kb (id, name, description, tenant_id, created_at, created_at_iso) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (kb_id, name, description, tenant_id, now, iso),
                )
                conn.close()
                return envelope(trace_id, data={"id": kb_id, "name": name, "description": description,
                             "tenant_id": tenant_id, "created_at_iso": iso})
            except Exception as e:
                return _err(trace_id, "KB_CREATE_FAILED", str(e))

        @self.app.get("/kb")
        async def kb_list(request: Request):
            trace_id = request.state.trace_id
            tenant = (request.query_params.get("tenant") or "default").strip()
            try:
                conn = _get_conn()
                cur = conn.execute(
                    "SELECT k.id, k.name, k.description, k.tenant_id, k.created_at, k.created_at_iso, "
                    "       (SELECT COUNT(*) FROM kb_doc d WHERE d.kb_id = k.id) AS doc_count "
                    "FROM kb k WHERE k.tenant_id = ? "
                    "ORDER BY k.created_at DESC",
                    (tenant,),
                )
                items = []
                for row in cur.fetchall():
                    item = _kb_row(row[:6])
                    item["doc_count"] = row[6]
                    items.append(item)
                conn.close()
                return envelope(trace_id, data={"count": len(items), "items": items})
            except Exception as e:
                return _err(trace_id, "KB_LIST_FAILED", str(e))

        @self.app.get("/kb/{kb_id}")
        async def kb_get(kb_id: str, request: Request):
            trace_id = request.state.trace_id
            try:
                conn = _get_conn()
                cur = conn.execute(
                    "SELECT id, name, description, tenant_id, created_at, created_at_iso "
                    "FROM kb WHERE id = ?", (kb_id,)
                )
                row = cur.fetchone()
                if not row:
                    conn.close()
                    return envelope(trace_id, code="NOT_FOUND", message=f"kb {kb_id!r} 不存在", status_code=404)
                kb = _kb_row(row)
                # 拉 doc 列表
                cur = conn.execute(
                    "SELECT doc_id, added_at FROM kb_doc WHERE kb_id = ? ORDER BY added_at",
                    (kb_id,),
                )
                docs = [{"doc_id": r[0], "added_at": r[1]} for r in cur.fetchall()]
                kb["docs"] = docs
                kb["doc_count"] = len(docs)
                conn.close()
                return envelope(trace_id, data=kb)
            except Exception as e:
                return _err(trace_id, "KB_GET_FAILED", str(e))

        @self.app.delete("/kb/{kb_id}")
        async def kb_delete(kb_id: str, request: Request):
            trace_id = request.state.trace_id
            try:
                conn = _get_conn()
                conn.execute("DELETE FROM kb WHERE id = ?", (kb_id,))
                conn.close()
                return envelope(trace_id, message="deleted", data={"id": kb_id})
            except Exception as e:
                return _err(trace_id, "KB_DELETE_FAILED", str(e))

        @self.app.post("/kb/{kb_id}/docs")
        async def kb_add_docs(kb_id: str, request: Request):
            trace_id = request.state.trace_id
            try:
                body = await request.json()
            except Exception:
                return _bad(trace_id, "请求体非合法 JSON")
            doc_ids = body.get("doc_ids") or []
            if not isinstance(doc_ids, list) or not doc_ids:
                return _bad(trace_id, "doc_ids 必须是非空数组")
            try:
                conn = _get_conn()
                # 确认 kb 存在
                cur = conn.execute("SELECT 1 FROM kb WHERE id = ?", (kb_id,))
                if not cur.fetchone():
                    conn.close()
                    return envelope(trace_id, code="NOT_FOUND", message=f"kb {kb_id!r} 不存在", status_code=404)
                now = int(time.time())
                added = 0
                for doc_id in doc_ids:
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO kb_doc (kb_id, doc_id, added_at) VALUES (?, ?, ?)",
                            (kb_id, str(doc_id), now),
                        )
                        added += 1
                    except Exception:
                        pass
                conn.close()
                return envelope(trace_id, data={"kb_id": kb_id, "added": added, "requested": len(doc_ids)})
            except Exception as e:
                return _err(trace_id, "KB_ADD_DOCS_FAILED", str(e))

        @self.app.delete("/kb/{kb_id}/docs/{doc_id}")
        async def kb_remove_doc(kb_id: str, doc_id: str, request: Request):
            trace_id = request.state.trace_id
            try:
                conn = _get_conn()
                conn.execute(
                    "DELETE FROM kb_doc WHERE kb_id = ? AND doc_id = ?",
                    (kb_id, doc_id),
                )
                conn.close()
                return envelope(trace_id, data={"kb_id": kb_id, "doc_id": doc_id})
            except Exception as e:
                return _err(trace_id, "KB_REMOVE_DOC_FAILED", str(e))


def _bad(trace_id: str, message: str) -> JSONResponse:
    return envelope(trace_id, code="BAD_REQUEST", message=message, status_code=400)


def _err(trace_id: str, code: str, message: str) -> JSONResponse:
    return envelope(trace_id, code=code, message=message, status_code=500, retryable=True)
