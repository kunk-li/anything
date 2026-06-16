# -*- coding: utf-8 -*-
"""
Projects (workspace) routes — 多项目支持 (Part B).

项目 = 一个可被 Agent 分析/操作的代码库, 用 root_path 指向其文件系统根。
与租户(数据隔离)解耦: 一个 tenant 下可注册多个项目; 前端选中哪个, 请求里带
extra_params.active_project_root, Agent 的提示词根 + ProjectMemory + 读码默认根就对准它
(见 prompt_builder._build_react_prompt 的 project_root 参数)。

端点:
    POST   /projects              {name, root_path} 注册一个项目 (校验 root_path 是真实目录)
    GET    /projects?tenant=...   list 当前 tenant 的项目
    DELETE /projects/{id}         删除注册 (只删登记, 不动磁盘上的项目文件)

存储: sqlite WAL (同 kb.py 风格), <ANYTHING_DATA_ROOT>/projects.sqlite3。
注: 注册表只是"项目清单"(给 UI 下拉用); "当前用哪个"是每次请求带的, 不在这里存状态。
"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import JSONResponse


_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    tenant_id TEXT DEFAULT 'default',
    created_at INTEGER NOT NULL,
    created_at_iso TEXT
);
CREATE INDEX IF NOT EXISTS idx_proj_tenant ON projects(tenant_id, created_at);
"""

_MEMORY_CANDIDATES = ("AGENTS.md", "CLAUDE.md", ".anything/memory.md")


def _get_db_path() -> Path:
    root = Path(os.environ.get("ANYTHING_DATA_ROOT", "run")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / "projects.sqlite3"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_get_db_path()), timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _has_memory_file(root_path: str) -> bool:
    base = Path(root_path)
    for cand in _MEMORY_CANDIDATES:
        try:
            if (base / cand).is_file():
                return True
        except OSError:
            continue
    return False


def _project_row(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "name": row[1],
        "root_path": row[2],
        "tenant_id": row[3],
        "created_at_iso": row[5],
        "has_project_memory": _has_memory_file(row[2]),
    }


class ProjectsRoutesMixin:
    """项目 (workspace) 注册表 router mixin."""

    def _register_projects_routes(self) -> None:

        @self.app.post("/projects")
        async def project_create(request: Request):
            trace_id = request.state.trace_id
            try:
                body = await request.json()
            except Exception:
                return _bad(trace_id, "请求体非合法 JSON")
            name = (body.get("name") or "").strip()
            if not name:
                return _bad(trace_id, "name 必填")
            root_path = (body.get("root_path") or "").strip()
            if not root_path:
                return _bad(trace_id, "root_path 必填 (项目在文件系统的根目录绝对路径)")
            # 校验是真实目录 (绝对化后存); Agent 会按这个根 dir/type 读码, 非目录直接拒。
            abs_root = os.path.abspath(os.path.expanduser(root_path))
            if not os.path.isdir(abs_root):
                return JSONResponse(
                    {"code": "PARAM_INVALID",
                     "message": f"root_path 不是一个存在的目录: {abs_root}",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": {"root_path": abs_root}},
                    status_code=400,
                )
            tenant_id = (body.get("tenant_id") or "default").strip() or "default"
            proj_id = "proj_" + uuid.uuid4().hex[:12]
            now = int(time.time())
            iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
            try:
                conn = _get_conn()
                conn.execute(
                    "INSERT INTO projects (id, name, root_path, tenant_id, created_at, created_at_iso) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (proj_id, name, abs_root, tenant_id, now, iso),
                )
                conn.close()
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": {"id": proj_id, "name": name, "root_path": abs_root,
                             "tenant_id": tenant_id, "created_at_iso": iso,
                             "has_project_memory": _has_memory_file(abs_root)},
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return _err(trace_id, "PROJECT_CREATE_FAILED", str(e))

        @self.app.get("/projects")
        async def project_list(request: Request):
            trace_id = request.state.trace_id
            tenant = (request.query_params.get("tenant") or "default").strip()
            try:
                conn = _get_conn()
                cur = conn.execute(
                    "SELECT id, name, root_path, tenant_id, created_at, created_at_iso "
                    "FROM projects WHERE tenant_id = ? ORDER BY created_at DESC",
                    (tenant,),
                )
                items = [_project_row(row) for row in cur.fetchall()]
                conn.close()
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": {"count": len(items), "items": items},
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return _err(trace_id, "PROJECT_LIST_FAILED", str(e))

        @self.app.delete("/projects/{proj_id}")
        async def project_delete(proj_id: str, request: Request):
            trace_id = request.state.trace_id
            try:
                conn = _get_conn()
                conn.execute("DELETE FROM projects WHERE id = ?", (proj_id,))
                conn.close()
                return JSONResponse({
                    "code": "SUCCESS", "message": "deleted",
                    "data": {"id": proj_id},
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return _err(trace_id, "PROJECT_DELETE_FAILED", str(e))


def _bad(trace_id: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"code": "BAD_REQUEST", "message": message,
         "data": None, "trace_id": trace_id,
         "retryable": False, "details": None},
        status_code=400,
    )


def _err(trace_id: str, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"code": code, "message": message,
         "data": None, "trace_id": trace_id,
         "retryable": True, "details": None},
        status_code=500,
    )
