"""API服务模块默认实现。"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from api_service_module.config.config import ApiServiceConfig
from api_service_module.core.base import BaseApiService
from api_service_module.model.data_model import IndexJob, UnifiedResponse
from api_service_module.utils.tool_functions import (
    build_error_details,
    ensure_directory,
    generate_job_id,
    generate_trace_id,
    safe_file_name,
    should_retry,
    standardize_request,
    utc_now_iso,
    validate_request_params,
)

logger = logging.getLogger("api_service_module")
logging.basicConfig(level=logging.INFO)


class InMemoryJobStore:
    """简单索引任务存储。"""

    def __init__(self) -> None:
        self._jobs: Dict[str, IndexJob] = {}

    def save(self, job: IndexJob) -> None:
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> Optional[IndexJob]:
        return self._jobs.get(job_id)


class LocalFolderIndexBuilder:
    """示例索引构建器。"""

    def build(self, source_type: str, source_path: str, chunking: Dict[str, Any]) -> Dict[str, Any]:
        if source_type != "local_folder":
            raise ValueError("当前仅支持 local_folder")
        folder = Path(source_path)
        if not folder.exists() or not folder.is_dir():
            raise FileNotFoundError(source_path)
        files = [p for p in folder.rglob("*") if p.is_file()]
        return {
            "indexed_files": len(files),
            "source_type": source_type,
            "source_path": str(folder),
            "chunking": chunking,
        }


class ApiService(BaseApiService):
    """标准 API 服务实现。"""

    def __init__(
        self,
        handler: Optional[Any] = None,
        config: Optional[ApiServiceConfig] = None,
        job_store: Optional[InMemoryJobStore] = None,
        index_builder: Optional[Any] = None,
        dependency_checker: Optional[Callable[[], Dict[str, str]]] = None,
    ) -> None:
        self.config = config or ApiServiceConfig()
        self.handler = handler
        self.job_store = job_store or InMemoryJobStore()
        self.index_builder = index_builder or LocalFolderIndexBuilder()
        self.dependency_checker = dependency_checker or (lambda: {"handler": "up" if self.handler else "down"})
        ensure_directory(self.config.upload_dir)
        ensure_directory(self.config.index_result_dir)
        self._request_total = 0
        self._request_errors = 0
        self._request_duration_total = 0.0
        self.app = self.create_app()

    def set_handler(self, handler: Any) -> None:
        self.handler = handler

    def set_index_builder(self, builder: Optional[Any]) -> None:
        if builder is not None:
            self.index_builder = builder

    def health_snapshot(self) -> Dict[str, Any]:
        return {
            "status": "UP",
            "dependencies": self.dependency_checker(),
            "handler_injected": self.handler is not None,
        }

    def create_app(self) -> FastAPI:
        app = FastAPI(title=self.config.app_name, version=self.config.app_version)

        @app.middleware("http")
        async def request_metrics_middleware(request: Request, call_next):
            trace_id = request.headers.get("X-Request-Id", generate_trace_id())
            start = time.perf_counter()
            try:
                request.state.trace_id = trace_id
                response = await call_next(request)
            except Exception as exc:
                self._request_errors += 1
                self._request_total += 1
                self._request_duration_total += time.perf_counter() - start
                logger.exception("未处理异常 trace_id=%s", trace_id)
                payload = UnifiedResponse(
                    code="UNKNOWN_ERROR",
                    message=str(exc) or "系统未知异常",
                    data=None,
                    trace_id=trace_id,
                    retryable=True,
                    details=build_error_details("UNKNOWN_ERROR"),
                    cost_time=time.perf_counter() - start,
                ).to_dict()
                return JSONResponse(status_code=500, content=payload)

            elapsed = time.perf_counter() - start
            self._request_total += 1
            self._request_duration_total += elapsed
            if response.status_code >= 400:
                self._request_errors += 1
            response.headers["X-Trace-Id"] = trace_id
            return response

        def api_key_guard(x_api_key: Optional[str] = Header(default=None)) -> None:
            if not self.config.auth_enabled:
                return
            if not x_api_key or x_api_key not in self.config.api_keys:
                raise HTTPException(
                    status_code=401,
                    detail=UnifiedResponse(
                        code="AUTH_REQUIRED",
                        message="未认证",
                        data=None,
                        trace_id=generate_trace_id(),
                        retryable=False,
                        details=build_error_details("AUTH_REQUIRED"),
                    ).to_dict(),
                )

        @app.exception_handler(HTTPException)
        async def http_exception_handler(_: Request, exc: HTTPException):
            if isinstance(exc.detail, dict) and "code" in exc.detail:
                payload = exc.detail
            else:
                trace_id = generate_trace_id()
                payload = UnifiedResponse(
                    code="BAD_REQUEST",
                    message=str(exc.detail),
                    data=None,
                    trace_id=trace_id,
                    retryable=False,
                    details=build_error_details("BAD_REQUEST", message=str(exc.detail)),
                ).to_dict()
            return JSONResponse(status_code=exc.status_code, content=payload)

        @app.post("/invoke", dependencies=[Depends(api_key_guard)])
        async def invoke(request: Request):
            trace_id = getattr(request.state, "trace_id", generate_trace_id())
            start = time.perf_counter()
            content_length = int(request.headers.get("content-length", "0") or 0)
            if content_length > self.config.max_request_size:
                payload = UnifiedResponse(
                    code="REQUEST_TOO_LARGE",
                    message="请求体超过大小限制",
                    data=None,
                    trace_id=trace_id,
                    retryable=False,
                    details=build_error_details(
                        "REQUEST_TOO_LARGE",
                        request={"content_length": content_length},
                    ),
                    cost_time=time.perf_counter() - start,
                ).to_dict()
                return JSONResponse(status_code=413, content=payload)

            body = await request.json()
            is_valid, error_message, error_code = validate_request_params(body)
            if not is_valid:
                payload = UnifiedResponse(
                    code=error_code,
                    message=error_message,
                    data=None,
                    trace_id=trace_id,
                    retryable=False,
                    details=build_error_details(error_code, body, error_message),
                    cost_time=time.perf_counter() - start,
                ).to_dict()
                return JSONResponse(status_code=400, content=payload)

            if self.handler is None:
                payload = UnifiedResponse(
                    code="ORCHESTRATOR_RUN_FAILED",
                    message="请求处理器尚未注入",
                    data=None,
                    trace_id=trace_id,
                    retryable=True,
                    details={"hint": "请在系统启动阶段注入 RequestHandler 实例"},
                    cost_time=time.perf_counter() - start,
                ).to_dict()
                return JSONResponse(status_code=500, content=payload)

            standardized = standardize_request(
                body,
                default_type=self.config.default_type,
                default_top_k=self.config.default_top_k,
            )
            result = self.handler.handle(standardized)
            result.setdefault("trace_id", trace_id)
            result.setdefault("retryable", should_retry(result.get("code", "SUCCESS")))
            result.setdefault("details", None)
            result.setdefault("cost_time", round(time.perf_counter() - start, 6))
            status_code = 200 if result.get("code") == "SUCCESS" else 500
            if result.get("code") in {"PARAM_MISSING", "PARAM_INVALID", "BAD_REQUEST"}:
                status_code = 400
            if result.get("code") == "AUTH_REQUIRED":
                status_code = 401
            if result.get("code") == "API_RATE_LIMITED":
                status_code = 429
            return JSONResponse(status_code=status_code, content=result)

        @app.post("/index/build", dependencies=[Depends(api_key_guard)])
        async def build_index(payload: Dict[str, Any]):
            job_id = generate_job_id()
            now = utc_now_iso()
            job = IndexJob(
                job_id=job_id,
                status="RUNNING",
                source_type=payload.get("source_type", "local_folder"),
                source_path=payload.get("source_path", ""),
                created_at=now,
                updated_at=now,
            )
            self.job_store.save(job)
            try:
                result = self.index_builder.build(
                    source_type=job.source_type,
                    source_path=job.source_path,
                    chunking=payload.get("chunking", {}),
                )
                job.status = "SUCCESS"
                job.result = result
                job.updated_at = utc_now_iso()
            except FileNotFoundError:
                job.status = "FAILED"
                job.error_code = "FOLDER_NOT_FOUND"
                job.error_message = f"文件夹不存在：{job.source_path}"
                job.updated_at = utc_now_iso()
            except Exception as exc:
                job.status = "FAILED"
                job.error_code = "VECTOR_UPSERT_FAILED"
                job.error_message = str(exc)
                job.updated_at = utc_now_iso()
            self.job_store.save(job)
            if job.status == "FAILED":
                payload = UnifiedResponse(
                    code=job.error_code or "UNKNOWN_ERROR",
                    message=job.error_message or "索引构建失败",
                    data=None,
                    trace_id=generate_trace_id(),
                    retryable=should_retry(job.error_code or "UNKNOWN_ERROR"),
                    details=build_error_details(job.error_code or "UNKNOWN_ERROR", {"source_path": job.source_path}),
                ).to_dict()
                return JSONResponse(status_code=400, content=payload)
            return JSONResponse(
                status_code=200,
                content=UnifiedResponse(
                    code="SUCCESS",
                    message="index build started",
                    data={"job_id": job_id},
                    trace_id=generate_trace_id(),
                    retryable=False,
                    details=None,
                ).to_dict(),
            )

        @app.get("/index/job/{job_id}", dependencies=[Depends(api_key_guard)])
        async def get_index_job(job_id: str):
            job = self.job_store.get(job_id)
            if job is None:
                payload = UnifiedResponse(
                    code="DOCUMENT_NOT_FOUND",
                    message=f"任务不存在：{job_id}",
                    data=None,
                    trace_id=generate_trace_id(),
                    retryable=False,
                    details={"job_id": job_id},
                ).to_dict()
                return JSONResponse(status_code=404, content=payload)
            return JSONResponse(
                status_code=200,
                content=UnifiedResponse(
                    code="SUCCESS",
                    message="ok",
                    data=job.to_dict(),
                    trace_id=generate_trace_id(),
                    retryable=False,
                    details=None,
                ).to_dict(),
            )

        @app.post("/documents/upload", dependencies=[Depends(api_key_guard)])
        async def upload_document(file: UploadFile = File(...), source: Optional[str] = None):
            target_dir = ensure_directory(self.config.upload_dir)
            target_path = target_dir / safe_file_name(file.filename or "upload.bin")
            content = await file.read()
            target_path.write_bytes(content)
            return JSONResponse(
                status_code=200,
                content=UnifiedResponse(
                    code="SUCCESS",
                    message="uploaded",
                    data={
                        "file_name": file.filename,
                        "stored_path": str(target_path),
                        "source": source,
                    },
                    trace_id=generate_trace_id(),
                    retryable=False,
                    details=None,
                ).to_dict(),
            )

        @app.get("/health")
        @app.get("/healthz")
        async def health():
            return JSONResponse(
                status_code=200,
                content=UnifiedResponse(
                    code="SUCCESS",
                    message="ok",
                    data=self.health_snapshot(),
                    trace_id=generate_trace_id(),
                    retryable=False,
                    details=None,
                ).to_dict(),
            )

        @app.get(self.config.readiness_name)
        async def ready():
            snapshot = self.health_snapshot()
            status_code = 200 if snapshot["dependencies"].get("handler") == "up" else 503
            return JSONResponse(status_code=status_code, content=snapshot)

        @app.get(self.config.liveness_name)
        async def live():
            return {"status": "alive"}

        @app.get("/metrics")
        async def metrics():
            avg_duration = self._request_duration_total / self._request_total if self._request_total else 0.0
            body = "\n".join(
                [
                    "# HELP http_requests_total Total HTTP requests",
                    "# TYPE http_requests_total counter",
                    f"http_requests_total {self._request_total}",
                    "# HELP http_request_errors_total Total HTTP error responses",
                    "# TYPE http_request_errors_total counter",
                    f"http_request_errors_total {self._request_errors}",
                    "# HELP http_request_duration_seconds_avg Average request duration seconds",
                    "# TYPE http_request_duration_seconds_avg gauge",
                    f"http_request_duration_seconds_avg {avg_duration}",
                ]
            )
            return PlainTextResponse(body)

        return app


app = ApiService().app
