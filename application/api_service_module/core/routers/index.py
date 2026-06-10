# -*- coding: utf-8 -*-
"""
IndexRoutesMixin (Task LL #72; P14 真实现 — 原 POST /index/build 是协议占位假接口)
POST /index/build         全量重建索引 (后台线程 + job 跟踪)
GET  /index/job/{job_id}  查任务真实状态/进度

重建语义: 清空向量库 + BM25 → 以 document_store 已存解析文档为源重 chunk/embed/灌入。
用途: 清理已删文档残留 (compaction) / chunk 参数变更 / 索引损坏修复。
重操作: 走 _check_admin (配置 security.admin_api_keys 后仅 admin key 可调)。
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse


class IndexRoutesMixin:
    """索引重建 + 任务查询路由."""

    def _register_index_routes(self) -> None:
        # job 状态表 (进程内): {job_id: {status, progress, result, error, ...}}
        # 单进程 uvicorn 够用; 多 worker 部署时 job 查询需打到同一 worker (后续接 StateBackend)
        self._index_jobs = {}
        self._index_jobs_lock = threading.Lock()

        @self.app.post("/index/build")
        async def build_index(request: Request):
            trace_id = request.state.trace_id
            if getattr(self, "rebuild_runner", None) is None:
                return JSONResponse(
                    status_code=501,
                    content={
                        "code": "SERVICE_UNAVAILABLE",
                        "message": "rebuild_runner 未注入, 索引重建不可用 (需 data_layer 完整装配)",
                        "data": None,
                        "trace_id": trace_id, "retryable": False, "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )
            denied = self._check_admin(request, trace_id)
            if denied is not None:
                return denied

            with self._index_jobs_lock:
                running = [jid for jid, j in self._index_jobs.items() if j.get("status") == "RUNNING"]
                if running:
                    return JSONResponse(
                        status_code=409,
                        content={
                            "code": "INDEX_REBUILD_RUNNING",
                            "message": "已有重建任务在执行, 请等它完成",
                            "data": {"job_id": running[0]},
                            "trace_id": trace_id, "retryable": True, "details": None,
                        },
                        headers={"X-Request-Id": trace_id},
                    )
                job_id = f"job_{uuid.uuid4().hex[:12]}"
                job = {
                    "job_id": job_id,
                    "status": "RUNNING",
                    "progress": {"done": 0, "total": None},
                    "result": None,
                    "error": None,
                    "started_at": round(time.time(), 3),
                    "finished_at": None,
                }
                self._index_jobs[job_id] = job

            def _progress(done: int, total: int) -> None:
                job["progress"] = {"done": done, "total": total}

            def _run() -> None:
                try:
                    result = self.rebuild_runner(progress_cb=_progress)
                    job["result"] = (result or {}).get("data")
                    job["status"] = "SUCCESS"
                except Exception as e:
                    job["status"] = "FAILED"
                    job["error"] = str(e)
                    self.logger.error(f"[index] rebuild failed: {e}\n{traceback.format_exc()}")
                finally:
                    job["finished_at"] = round(time.time(), 3)

            threading.Thread(target=_run, name=f"index-rebuild-{job_id}", daemon=True).start()
            self.logger.info(f"[index] rebuild job started: {job_id}")
            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "index rebuild started",
                    "data": {"job_id": job_id, "status": "RUNNING"},
                    "trace_id": trace_id, "retryable": False, "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.get("/index/job/{job_id}")
        async def get_index_job(job_id: str, request: Request):
            trace_id = request.state.trace_id
            with self._index_jobs_lock:
                job = self._index_jobs.get(job_id)
            if job is None:
                return JSONResponse(
                    status_code=404,
                    content={
                        "code": "JOB_NOT_FOUND",
                        "message": f"job 不存在: {job_id} (进程重启后 job 记录不保留)",
                        "data": None,
                        "trace_id": trace_id, "retryable": False, "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )
            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "ok",
                    "data": dict(job),
                    "trace_id": trace_id, "retryable": False, "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )
