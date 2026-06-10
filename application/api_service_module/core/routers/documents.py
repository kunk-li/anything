# -*- coding: utf-8 -*-
"""
DocumentsRoutesMixin (Task LL #72)
POST   /documents/upload          上传 + 自动索引
GET    /documents                 列已索引 (Task JJ)
DELETE /documents/{doc_id}        删除 + 摘向量库 (Task JJ)
GET    /documents/{doc_id}/preview chunk 跳转预览
"""

from __future__ import annotations

import asyncio
import traceback
from pathlib import Path
from typing import List

from fastapi import Request, UploadFile, File
from fastapi.responses import JSONResponse


class DocumentsRoutesMixin:
    """文档管理路由."""

    def _register_documents_routes(self) -> None:
        @self.app.post("/documents/upload")
        async def upload_document(request: Request, file: UploadFile = File(...)):
            trace_id = request.state.trace_id

            # 协议层: 落盘到 upload_dir
            upload_dir = self.config.get_config("api_service.upload_dir", "./uploads")
            Path(upload_dir).mkdir(parents=True, exist_ok=True)

            # 客户端 filename 不可信: 剥目录部分 (防 ../ 穿越写到 upload_dir 外),
            # 统一两种分隔符 (curl 可伪造 "..\\evil" 而 POSIX Path.name 不认反斜杠)
            raw_name = (file.filename or "").replace("\\", "/")
            safe_name = Path(raw_name).name.strip()
            if not safe_name or safe_name in (".", ".."):
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "PARAM_INVALID",
                        "message": f"非法文件名: {file.filename!r}",
                        "data": None,
                        "trace_id": trace_id, "retryable": False, "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )
            upload_root = Path(upload_dir).resolve()
            file_path = upload_root / safe_name
            if file_path.resolve().parent != upload_root:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "PARAM_INVALID",
                        "message": "文件名解析后越出上传目录, 已拒绝",
                        "data": None,
                        "trace_id": trace_id, "retryable": False, "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )
            # 同名不再静默覆盖: 加 -N 后缀 (重复内容会在索引层被 content-hash 查重跳过)
            if file_path.exists():
                stem, suffix = file_path.stem, file_path.suffix
                for i in range(1, 10000):
                    cand = upload_root / f"{stem}-{i}{suffix}"
                    if not cand.exists():
                        file_path = cand
                        break

            # 分块落盘 (1MB/块): 大文件不整体读进内存 (原 await file.read() 上传
            # 100MB 知识库就吃 100MB RAM, 多租户并发上传会 OOM)
            with file_path.open("wb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)

            response_data = {
                "file_name": file_path.name,
                "stored_path": str(file_path),
                "indexed": False,
            }

            # 如果注入了 index_runner, 上传后立刻索引到默认 tenant 的 vector_store。
            # 这是单 worker 同步操作 (parse + chunk + embed + upsert), 一般 1-3 秒。
            if self.index_runner is not None:
                import asyncio as _asyncio
                try:
                    loop = _asyncio.get_event_loop()
                    idx_result = await loop.run_in_executor(
                        None, lambda: self.index_runner(str(file_path))
                    )
                    response_data["indexed"] = True
                    response_data["index_summary"] = {
                        "total_chunks": idx_result.get("data", {}).get("total_chunks", 0),
                        "total_vectors": idx_result.get("data", {}).get("total_vectors", 0),
                        "documents": idx_result.get("data", {}).get("documents", []),
                    }
                    self.logger.info(
                        f"[upload+index] file={file.filename} "
                        f"chunks={response_data['index_summary']['total_chunks']} "
                        f"vectors={response_data['index_summary']['total_vectors']}"
                    )
                except Exception as e:
                    # 索引失败不阻塞上传成功 (文件已落盘), 仅 ERROR 日志
                    self.logger.error(
                        f"[upload+index] indexing failed for {file.filename}: {e}\n"
                        f"{traceback.format_exc()}"
                    )
                    response_data["index_error"] = str(e)

            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "uploaded" + (" + indexed" if response_data["indexed"] else ""),
                    "data": response_data,
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        # Task JJ (#70): 文档管理 — list / delete
        @self.app.get("/documents")
        async def list_documents(request: Request):
            """列出当前 tenant 已索引文档. 跟 /admin/status 同等权限 (生产网关加 RBAC)."""
            trace_id = request.state.trace_id
            if self.document_store_factory is None:
                return JSONResponse(
                    status_code=501,
                    content={
                        "code": "SERVICE_UNAVAILABLE",
                        "message": "document_store_factory 未注入",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )
            tid = self._resolve_tenant_from_auth(request) or "default"
            try:
                store = self.document_store_factory(tid)
                if not hasattr(store, "list_documents"):
                    return JSONResponse(
                        status_code=501,
                        content={
                            "code": "SERVICE_UNAVAILABLE",
                            "message": "doc_store 不支持 list_documents",
                            "data": None,
                            "trace_id": trace_id,
                            "retryable": False,
                            "details": None,
                        },
                        headers={"X-Request-Id": trace_id},
                    )
                docs = store.list_documents()
            except Exception as e:
                self.logger.error(f"[documents.list] tenant={tid} err={e}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "code": "UNKNOWN_ERROR",
                        "message": str(e),
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )
            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "ok",
                    "data": {
                        "tenant_id": tid,
                        "count": len(docs),
                        "documents": docs,
                    },
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.delete("/documents/{doc_id}")
        async def delete_document(doc_id: str, request: Request):
            """删除文档 — 五处一次清干净, 各处结果如实回报:

            1. document_store 解析文档 (正文 + info.json + hash map)
            2. vector_db 向量 (IndexIDMap2 真删除, 按 doc_id filter)
            3. BM25 倒排条目 (remove_doc + 持久化)
            4. uploads/ 原始上传文件 (按 info.stored_path 回收, 限 upload_dir 内)
            5. kb.sqlite3 的 kb_doc 关联行 (防悬挂引用)
            """
            trace_id = request.state.trace_id
            if self.document_store_factory is None:
                return JSONResponse(
                    status_code=501,
                    content={
                        "code": "SERVICE_UNAVAILABLE",
                        "message": "document_store_factory 未注入",
                        "data": None,
                        "trace_id": trace_id, "retryable": False, "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )
            tid = self._resolve_tenant_from_auth(request) or "default"

            deleted_doc = False
            deleted_vectors = False
            bm25_removed = 0
            deleted_upload_file = False
            kb_links_removed = 0
            warnings: List[str] = []
            stored_path = None
            try:
                store = self.document_store_factory(tid)
                # 删之前先读 info 拿原始上传文件路径 (删完就读不到了)
                try:
                    info = store.read_info_file(doc_id) or {}
                    stored_path = info.get("stored_path")
                except Exception:
                    stored_path = None
                try:
                    deleted_doc = bool(store.delete_document(doc_id))
                except Exception as e:
                    warnings.append(f"document_store: {e}")
            except Exception as e:
                warnings.append(f"document_store_factory: {e}")

            if self.vector_db is not None:
                try:
                    deleted_vectors = bool(self.vector_db.delete(filters={"doc_id": doc_id}))
                except Exception as e:
                    warnings.append(f"vector_db: {e}")

            if self.bm25_retriever is not None:
                try:
                    bm25_removed = int(self.bm25_retriever.remove_doc(doc_id))
                    if bm25_removed and self.bm25_index_path:
                        self.bm25_retriever.save(self.bm25_index_path)
                except Exception as e:
                    warnings.append(f"bm25: {e}")

            # uploads/ 原件回收 — 只删 upload_dir 直系子文件, 路径在外一律不动
            if stored_path:
                try:
                    upload_root = Path(
                        self.config.get_config("api_service.upload_dir", "./uploads")
                    ).resolve()
                    p = Path(stored_path).resolve()
                    if p.parent == upload_root and p.is_file():
                        p.unlink()
                        deleted_upload_file = True
                except Exception as e:
                    warnings.append(f"upload_file: {e}")

            # kb_doc 关联清理 — kb 库不存在时跳过 (不无故建库文件)
            try:
                from .kb import _get_db_path
                import sqlite3 as _sqlite3
                kb_db = _get_db_path()
                if kb_db.exists():
                    conn = _sqlite3.connect(str(kb_db), timeout=5.0)
                    try:
                        cur = conn.execute("DELETE FROM kb_doc WHERE doc_id = ?", (doc_id,))
                        kb_links_removed = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                        conn.commit()
                    finally:
                        conn.close()
            except Exception as e:
                warnings.append(f"kb: {e}")

            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS" if deleted_doc else "NOT_FOUND",
                    "message": "deleted" if deleted_doc else "doc_id not found in document_store",
                    "data": {
                        "doc_id": doc_id,
                        "tenant_id": tid,
                        "deleted_from_document_store": deleted_doc,
                        "deleted_from_vector_db": deleted_vectors,
                        "bm25_chunks_removed": bm25_removed,
                        "deleted_upload_file": deleted_upload_file,
                        "kb_links_removed": kb_links_removed,
                        "warnings": warnings,
                    },
                    "trace_id": trace_id, "retryable": False, "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.get("/documents/{doc_id}/preview")
        async def get_document_preview(
            doc_id: str,
            request: Request,
            start_char: int = 0,
            end_char: int = 0,
            context: int = 200,
        ):
            """文档预览 — 按 chunk 的 [start_char, end_char] 范围抠一段带上下文的原文。

            Query params:
                start_char: chunk 起始字符位置 (来自 RetrievedChunk.start_char)
                end_char:   chunk 结束字符位置
                context:    高亮区上下文前后各 N 字符 (默认 200, 上限 2000)

            响应 data:
                doc_id, file_name, file_type, total_chars,
                snippet (字符串), snippet_start, snippet_end,
                highlight_start, highlight_end (相对于 snippet 的偏移)

            §9.3 防越权: tenant 取自认证产物;
            未认证且非 internal IP 时,body/query 的 tenant_id 已被剥除,走 default。
            """
            trace_id = request.state.trace_id

            if self.document_store_factory is None:
                # 工厂未注入 -> 该功能不可用 (纯 API 部署没装上文档预览)
                return JSONResponse(
                    status_code=501,
                    content={
                        "code": "PREVIEW_NOT_SUPPORTED",
                        "message": "文档预览未启用 (document_store_factory 未注入)",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )

            # tenant 解析: auth 优先, 否则 query 参数 tenant_id (仅 internal IP), 否则 default
            tid = self._resolve_tenant_from_auth(request)
            if not tid:
                qtid = request.query_params.get("tenant_id")
                if qtid and self._is_internal_ip(request):
                    tid = qtid
                else:
                    tid = "default"

            if not self._is_known_tenant(tid):
                return JSONResponse(
                    status_code=404,
                    content={
                        "code": "TENANT_NOT_FOUND",
                        "message": "tenant 不存在或无权访问",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )

            try:
                store = self.document_store_factory(tid)
            except Exception as e:
                self.logger.error(f"document_store_factory 失败: tenant={tid} err={e}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "code": "UNKNOWN_ERROR",
                        "message": "文档存储初始化失败",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )

            # get_document 自动隔离到 <storage>/<tid>/ 子目录
            try:
                doc = store.get_document(doc_id)
            except ValueError:
                # 非法 doc_id (非 uuid4)
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "PARAM_INVALID",
                        "message": "doc_id 格式非法",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": {"field": "doc_id"},
                    },
                    headers={"X-Request-Id": trace_id},
                )
            if not doc:
                # §9.3 防枚举: 跨租户 / 不存在统一 DOCUMENT_NOT_FOUND
                return JSONResponse(
                    status_code=404,
                    content={
                        "code": "DOCUMENT_NOT_FOUND",
                        "message": "文档不存在",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": {"doc_id": doc_id},
                    },
                    headers={"X-Request-Id": trace_id},
                )

            content_str = str(doc.get("content") or "")
            total = len(content_str)

            # 清洗 + 兜底: 把窗口卡在 [0, total]
            ctx = max(0, min(int(context or 200), 2000))
            s = max(0, int(start_char or 0))
            e = max(s, int(end_char or s))
            s = min(s, total)
            e = min(e, total)
            snippet_start = max(0, s - ctx)
            snippet_end = min(total, e + ctx)
            snippet = content_str[snippet_start:snippet_end]

            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "ok",
                    "data": {
                        "doc_id": doc_id,
                        "file_name": doc.get("file_name"),
                        "file_type": doc.get("file_type"),
                        "total_chars": total,
                        "snippet": snippet,
                        "snippet_start": snippet_start,
                        "snippet_end": snippet_end,
                        "highlight_start": s - snippet_start,
                        "highlight_end": e - snippet_start,
                    },
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )
