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
import string
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import JSONResponse
from ._envelope import envelope

from project_memory_module.impl import validate_workspace_root, get_fs_root


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


_MAX_DIRS = 1000


def _browse_dir(raw: str) -> Dict[str, Any]:
    """只读列目录给前端目录浏览器。raw 空 → 盘符(Windows)/根(posix)/沙箱根(设了 jail);
    否则列子目录(不列文件)。设了 ANYTHING_FS_ROOT 时, 浏览被钉死在沙箱根内 (越界 → PermissionError)。
    可能抛 OSError 子类 (PermissionError/FileNotFoundError/NotADirectoryError), 由路由转 HTTP 码。"""
    raw = (raw or "").strip()
    fs_root = get_fs_root()
    if not raw:
        if fs_root:
            base = fs_root          # jail 开: 空 → 列沙箱根本身, 不暴露盘符
        elif os.name == "nt":
            drives = [{"name": f"{d}:\\", "path": f"{d}:\\", "has_project_memory": False}
                      for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            return {"path": "", "parent": None, "is_root": True,
                    "dirs": drives, "has_project_memory": False}
        else:
            base = "/"
    else:
        # 浏览模式: 盘符根可列 (导航需要), 但 jail 边界仍生效。
        norm, why = validate_workspace_root(raw, for_browse=True)
        if why == "outside_jail":
            raise PermissionError(raw)
        if why is not None:
            raise NotADirectoryError(raw)
        base = norm
    base = os.path.abspath(os.path.expanduser(base))
    if not os.path.isdir(base):
        raise NotADirectoryError(base)
    dirs = []
    for name in sorted(os.listdir(base), key=lambda s: s.lower()):
        full = os.path.join(base, name)
        try:
            if os.path.isdir(full):
                dirs.append({"name": name, "path": full,
                             "has_project_memory": _has_memory_file(full)})
                if len(dirs) >= _MAX_DIRS:
                    break
        except OSError:
            continue
    parent = os.path.dirname(base)
    if fs_root and os.path.normcase(base) == os.path.normcase(fs_root):
        parent = None  # jail 开: 到沙箱根就到顶, 不让 ⬆ 爬出去
    elif parent == base:  # 盘符根 / posix '/' → 上一级回到盘符列表(Windows)或无上级(posix)
        parent = "" if os.name == "nt" else None
    return {"path": base, "parent": parent, "is_root": False,
            "dirs": dirs, "has_project_memory": _has_memory_file(base)}


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
            # 同一道闸: 真实目录 + 非盘符根/系统根(防手滑) + ANYTHING_FS_ROOT 沙箱内(若设)。
            abs_root = os.path.abspath(os.path.expanduser(root_path))
            norm, why = validate_workspace_root(root_path)
            if why is not None:
                msg = {
                    "not_a_dir": f"root_path 不是一个存在的目录: {abs_root}",
                    "drive_root": f"不能把盘符根/系统根目录设为工作区: {abs_root}",
                    "outside_jail": f"该目录不在允许的工作区范围 (ANYTHING_FS_ROOT) 内: {abs_root}",
                }.get(why, f"root_path 无效: {abs_root}")
                return envelope(trace_id, code="PARAM_INVALID", message=msg, status_code=400, details={"root_path": abs_root, "reason": why})
            abs_root = norm  # 用规范化后的根做去重/存储
            # 三端 (create/list/delete) tenant 来源对齐: 鉴权开时以认证产物为准 (忽略 body 声明,
            # 防越权 + 保证 create 落库 tenant 与 delete 限定 tenant 一致, 否则 owner 删不掉自己建的项目);
            # 鉴权关 (dev/单租户) 时回落 body/default, 行为不变。
            tenant_id = self._resolve_tenant_from_auth(request) or (body.get("tenant_id") or "default").strip() or "default"
            proj_id = "proj_" + uuid.uuid4().hex[:12]
            now = int(time.time())
            iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
            try:
                # closing(): 任一 execute 抛错时旧代码跳过 conn.close() → WAL 句柄泄漏
                with closing(_get_conn()) as conn:
                    # 幂等去重: 同 tenant 下同一根路径已注册 → 直接返回那个, 不再建重复项。
                    cur = conn.execute(
                        "SELECT id, name, root_path, tenant_id, created_at_iso "
                        "FROM projects WHERE tenant_id = ? AND root_path = ?",
                        (tenant_id, abs_root),
                    )
                    ex = cur.fetchone()
                    if ex:
                        return envelope(trace_id, message="already exists", data={"id": ex[0], "name": ex[1], "root_path": ex[2],
                                     "tenant_id": ex[3], "created_at_iso": ex[4],
                                     "has_project_memory": _has_memory_file(ex[2])})
                    conn.execute(
                        "INSERT INTO projects (id, name, root_path, tenant_id, created_at, created_at_iso) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (proj_id, name, abs_root, tenant_id, now, iso),
                    )
                return envelope(trace_id, data={"id": proj_id, "name": name, "root_path": abs_root,
                             "tenant_id": tenant_id, "created_at_iso": iso,
                             "has_project_memory": _has_memory_file(abs_root)})
            except Exception as e:
                return _err(trace_id, "PROJECT_CREATE_FAILED", str(e))

        @self.app.get("/projects")
        async def project_list(request: Request):
            trace_id = request.state.trace_id
            # 同 create: 鉴权开时按认证租户列 (防 ?tenant=B 越权读他人项目); 关时回落 query/default。
            tenant = self._resolve_tenant_from_auth(request) or (request.query_params.get("tenant") or "default").strip()
            try:
                with closing(_get_conn()) as conn:
                    cur = conn.execute(
                        "SELECT id, name, root_path, tenant_id, created_at, created_at_iso "
                        "FROM projects WHERE tenant_id = ? ORDER BY created_at DESC",
                        (tenant,),
                    )
                    items = [_project_row(row) for row in cur.fetchall()]
                return envelope(trace_id, data={"count": len(items), "items": items})
            except Exception as e:
                return _err(trace_id, "PROJECT_LIST_FAILED", str(e))

        @self.app.delete("/projects/{proj_id}")
        async def project_delete(proj_id: str, request: Request):
            trace_id = request.state.trace_id
            # 认证开启时只允许删本租户的项目: 否则泄漏/猜到的 id 可被跨租户删除
            # (create/list 都按 tenant 隔离, 唯独 delete 漏了)。未认证维持旧行为。
            tid = self._resolve_tenant_from_auth(request)
            try:
                with closing(_get_conn()) as conn:
                    if tid:
                        cur = conn.execute(
                            "DELETE FROM projects WHERE id = ? AND tenant_id = ?",
                            (proj_id, tid),
                        )
                    else:
                        cur = conn.execute("DELETE FROM projects WHERE id = ?", (proj_id,))
                    deleted = cur.rowcount
                if deleted == 0:
                    # 旧代码无论 id 是否存在都回 SUCCESS; 现按实际删除行数报 NOT_FOUND
                    return envelope(trace_id, code="NOT_FOUND", message=f"项目不存在: {proj_id}", data={"id": proj_id}, status_code=404)
                return envelope(trace_id, message="deleted", data={"id": proj_id})
            except Exception as e:
                return _err(trace_id, "PROJECT_DELETE_FAILED", str(e))

        @self.app.get("/projects/fs")
        async def project_fs_browse(request: Request):
            """只读列目录 — 给前端做"服务器目录浏览器"选项目根用 (浏览器拿不到本地绝对路径)。
            path 空 → Windows 列盘符 / 类 Unix 列 '/'; 否则列该目录下的子目录 (不列文件)。
            仅列目录名 + 标注有无 AGENTS.md, 不读文件内容。访问受现有鉴权约束。"""
            trace_id = request.state.trace_id
            raw = request.query_params.get("path", "") or ""
            try:
                return envelope(trace_id, data=_browse_dir(raw))
            except PermissionError:
                return envelope(trace_id, code="PERMISSION_DENIED", message=f"无权访问该目录: {raw}", status_code=403)
            except (FileNotFoundError, NotADirectoryError):
                return envelope(trace_id, code="NOT_FOUND", message=f"目录不存在: {raw}", status_code=404)
            except Exception as e:
                return _err(trace_id, "PROJECT_FS_FAILED", str(e))


def _bad(trace_id: str, message: str) -> JSONResponse:
    return envelope(trace_id, code="BAD_REQUEST", message=message, status_code=400)


def _err(trace_id: str, code: str, message: str) -> JSONResponse:
    return envelope(trace_id, code=code, message=message, status_code=500, retryable=True)
