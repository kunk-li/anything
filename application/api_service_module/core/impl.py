# -*- coding: utf-8 -*-
"""
API 服务模块具体实现类
负责 HTTP 协议层处理、鉴权、中间件、trace_id 透传与统一错误映射
"""

import json
import threading
import time
import uuid
from typing import Dict, Any, Optional

from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse, PlainTextResponse

from deps_module import BasicDeps, build_basic_deps


class ApiService:
    """标准 API 服务实现：只负责 HTTP 协议层，不承载业务语义校验"""

    def __init__(self, handler, deps: Optional[BasicDeps] = None):
        # 基础依赖优先走 DI 注入
        deps = deps or build_basic_deps()
        self.utils = deps.utils
        self.logger = deps.logger
        self.config = deps.config
        self.exception_handler = deps.exception_handler

        self.handler = handler

        self.app = FastAPI(
            title=self.config.get_config("api_service.title", "RAG & Agent API"),
            version=self.config.get_config("api_service.version", "1.0.0"),
        )

        self.auth_enabled = bool(self.config.get_config("security.auth_enabled", False))
        self.auth_type = self.config.get_config("security.auth_type", "none")
        self.api_keys = self.config.get_config("security.api_keys", []) or []
        self.max_body_size = int(self.config.get_config("api_service.max_body_size", 1048576))
        self.enable_health_details = bool(
            self.config.get_config("api_service.enable_health_details", True)
        )

        # 简单的 in-memory metrics, 通过 /metrics 端点暴露为 Prometheus 文本格式
        # (单进程跑 uvicorn 够用; 多 worker 需后续接 prometheus_client + multiprocess mode)
        self._metrics_lock = threading.Lock()
        self._metrics: Dict[str, Dict[str, float]] = {
            "requests_by_type": {},        # type ("rag"/"agent"/"hybrid"/"unknown") -> count
            "errors_by_code": {},          # error code -> count
            "duration_sum_by_type": {},    # type -> sum(seconds)
            "duration_count_by_type": {},  # type -> count(用于算平均)
        }

        self._register_middlewares()
        self._register_routes()
        self._register_exception_handlers()

        self.logger.info("API 服务模块初始化完成")

    def _record_metrics(self, req_type: str, code: str, duration: float) -> None:
        """更新 metrics 计数. 持锁是因为 FastAPI 可能并发处理多请求."""
        with self._metrics_lock:
            req_type = req_type or "unknown"
            self._metrics["requests_by_type"][req_type] = (
                self._metrics["requests_by_type"].get(req_type, 0) + 1
            )
            if code != "SUCCESS":
                self._metrics["errors_by_code"][code] = (
                    self._metrics["errors_by_code"].get(code, 0) + 1
                )
            self._metrics["duration_sum_by_type"][req_type] = (
                self._metrics["duration_sum_by_type"].get(req_type, 0.0) + duration
            )
            self._metrics["duration_count_by_type"][req_type] = (
                self._metrics["duration_count_by_type"].get(req_type, 0) + 1
            )

    def _render_prometheus_metrics(self) -> str:
        """把 in-memory metrics 渲染为 Prometheus 文本格式."""
        with self._metrics_lock:
            snapshot = {
                k: dict(v) for k, v in self._metrics.items()
            }

        lines = []

        lines.append("# HELP anything_requests_total Total RAG/Agent requests handled")
        lines.append("# TYPE anything_requests_total counter")
        for t, n in snapshot["requests_by_type"].items():
            lines.append(f'anything_requests_total{{type="{t}"}} {int(n)}')

        lines.append("")
        lines.append("# HELP anything_errors_total Total non-SUCCESS responses by code")
        lines.append("# TYPE anything_errors_total counter")
        for code, n in snapshot["errors_by_code"].items():
            lines.append(f'anything_errors_total{{code="{code}"}} {int(n)}')

        lines.append("")
        lines.append("# HELP anything_request_duration_seconds_sum Cumulative request duration")
        lines.append("# TYPE anything_request_duration_seconds_sum counter")
        for t, s in snapshot["duration_sum_by_type"].items():
            lines.append(f'anything_request_duration_seconds_sum{{type="{t}"}} {s:.6f}')

        lines.append("")
        lines.append("# HELP anything_request_duration_seconds_count Total samples for duration")
        lines.append("# TYPE anything_request_duration_seconds_count counter")
        for t, n in snapshot["duration_count_by_type"].items():
            lines.append(f'anything_request_duration_seconds_count{{type="{t}"}} {int(n)}')

        return "\n".join(lines) + "\n"

    # =========================
    # 中间件与异常处理
    # =========================
    def _register_middlewares(self) -> None:
        @self.app.middleware("http")
        async def trace_middleware(request: Request, call_next):
            trace_id = request.headers.get("X-Request-Id") or self._generate_trace_id()
            request.state.trace_id = trace_id
            request.state.start_time = time.time()

            try:
                response = await call_next(request)
                response.headers["X-Request-Id"] = trace_id
                return response
            except Exception as e:
                self.logger.error(f"API 中间件异常：trace_id={trace_id}, error={str(e)}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "code": "UNKNOWN_ERROR",
                        "message": "服务内部异常",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )

    def _register_exception_handlers(self) -> None:
        @self.app.exception_handler(HTTPException)
        async def http_exception_handler(request: Request, exc: HTTPException):
            trace_id = getattr(request.state, "trace_id", None) or self._generate_trace_id()
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "code": "BAD_REQUEST" if exc.status_code < 500 else "UNKNOWN_ERROR",
                    "message": exc.detail,
                    "data": None,
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.exception_handler(Exception)
        async def global_exception_handler(request: Request, exc: Exception):
            trace_id = getattr(request.state, "trace_id", None) or self._generate_trace_id()
            self.logger.error(f"全局异常：trace_id={trace_id}, error={str(exc)}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "code": "UNKNOWN_ERROR",
                    "message": "服务内部异常",
                    "data": None,
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

    # =========================
    # 路由注册
    # =========================
    def _register_routes(self) -> None:
        @self.app.post("/invoke")
        async def invoke(request: Request):
            trace_id = request.state.trace_id
            start_time = time.time()

            # 1. 鉴权（协议层）
            auth_error = self._check_auth(request, trace_id)
            if auth_error is not None:
                return auth_error

            # 2. 请求体大小限制（协议层）
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > self.max_body_size:
                        return JSONResponse(
                            status_code=413,
                            content={
                                "code": "PARAM_INVALID",
                                "message": f"请求体过大，超过限制 {self.max_body_size} 字节",
                                "data": None,
                                "trace_id": trace_id,
                                "retryable": False,
                                "details": {
                                    "field": "content-length",
                                    "expected": f"<= {self.max_body_size}",
                                    "actual": content_length,
                                },
                            },
                            headers={"X-Request-Id": trace_id},
                        )
                except ValueError:
                    pass

            # 3. 解析 JSON（协议层）
            try:
                body = await request.json()
            except json.JSONDecodeError:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "BAD_REQUEST",
                        "message": "请求体不是合法 JSON",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": {
                            "field": "body",
                            "expected": "application/json",
                            "actual": "invalid json",
                        },
                    },
                    headers={"X-Request-Id": trace_id},
                )

            if not isinstance(body, dict):
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "BAD_REQUEST",
                        "message": "请求体必须是 JSON 对象",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": {
                            "field": "body",
                            "expected": "json object",
                            "actual": str(type(body).__name__),
                        },
                    },
                    headers={"X-Request-Id": trace_id},
                )

            # 4. 透传到接口层：业务语义校验交给 handler
            result = self.handler.handle(body, trace_id=trace_id)

            # 5. 应用层只负责业务码 -> HTTP 状态码映射
            http_status = self._map_code_to_http_status(result.get("code", "UNKNOWN_ERROR"))
            duration = time.time() - start_time
            headers = {"X-Request-Id": trace_id}
            headers["X-Cost-Time"] = str(round(duration, 3))

            # 6. 记录 metrics (异步级别开销极低)
            self._record_metrics(
                req_type=str(body.get("type", "unknown")),
                code=str(result.get("code", "UNKNOWN_ERROR")),
                duration=duration,
            )

            return JSONResponse(
                status_code=http_status,
                content=result,
                headers=headers,
            )

        @self.app.get("/health")
        async def health(request: Request):
            trace_id = request.state.trace_id
            data = {"status": "UP"}
            if self.enable_health_details:
                data["dependencies"] = {
                    "handler": "UP" if self.handler is not None else "DOWN"
                }

            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "ok",
                    "data": data,
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.get("/healthz")
        async def healthz(request: Request):
            trace_id = request.state.trace_id
            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "ok",
                    "data": {"status": "UP"},
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.get("/metrics")
        async def metrics(request: Request):
            """Prometheus 文本格式 metrics 端点.

            可以直接被 Prometheus / VictoriaMetrics 抓取,无需中间件.
            示例 prometheus.yml scrape_config:
                - job_name: anything
                  static_configs:
                    - targets: ['localhost:8000']
            """
            return PlainTextResponse(
                content=self._render_prometheus_metrics(),
                status_code=200,
                headers={"X-Request-Id": request.state.trace_id},
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )

        @self.app.post("/documents/upload")
        async def upload_document(request: Request, file: UploadFile = File(...)):
            trace_id = request.state.trace_id

            # 协议层：仅负责上传与返回落盘信息，不做索引编排
            upload_dir = self.config.get_config("api_service.upload_dir", "./uploads")
            Path(upload_dir).mkdir(parents=True, exist_ok=True)

            file_path = Path(upload_dir) / file.filename
            content = await file.read()
            file_path.write_bytes(content)

            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "uploaded",
                    "data": {
                        "file_name": file.filename,
                        "stored_path": str(file_path),
                    },
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.post("/index/build")
        async def build_index(request: Request):
            trace_id = request.state.trace_id
            # 当前阶段保留协议占位，不在 API 层直接编排 parser/embedding/vector_db
            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "index build started",
                    "data": {
                        "job_id": f"job_{uuid.uuid4().hex[:12]}"
                    },
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.get("/index/job/{job_id}")
        async def get_index_job(job_id: str, request: Request):
            trace_id = request.state.trace_id
            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "ok",
                    "data": {
                        "job_id": job_id,
                        "status": "PENDING",
                    },
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

    # =========================
    # 辅助方法
    # =========================
    def _check_auth(self, request: Request, trace_id: str) -> Optional[JSONResponse]:
        """鉴权检查：仅处理协议层鉴权，不涉及业务逻辑"""
        if not self.auth_enabled or self.auth_type == "none":
            return None

        if self.auth_type == "apikey":
            api_key = request.headers.get("X-API-Key")
            if not api_key or api_key not in self.api_keys:
                return JSONResponse(
                    status_code=401,
                    content={
                        "code": "AUTH_REQUIRED",
                        "message": "未认证或 API Key 无效",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )

        # 其他认证方式先保留占位
        return None

    def _generate_trace_id(self) -> str:
        return uuid.uuid4().hex

    def _map_code_to_http_status(self, code: str) -> int:
        """应用层负责业务码 -> HTTP 状态码映射"""
        success_codes = {"SUCCESS"}
        bad_request_codes = {"PARAM_MISSING", "PARAM_INVALID", "BAD_REQUEST", "TOOL_NOT_FOUND"}
        unauthorized_codes = {"AUTH_REQUIRED"}
        forbidden_codes = {"AUTH_FORBIDDEN"}
        not_found_codes = {"DOCUMENT_NOT_FOUND", "FOLDER_NOT_FOUND", "STATE_NOT_FOUND"}
        unsupported_codes = {"UNSUPPORTED_FILE_TYPE"}
        rate_limit_codes = {"API_RATE_LIMITED"}
        timeout_codes = {"AGENT_TIMEOUT", "LLM_TIMEOUT"}
        not_implemented_codes = {"VECTOR_DELETE_NOT_SUPPORTED"}

        if code in success_codes:
            return 200
        if code in bad_request_codes:
            return 400
        if code in unauthorized_codes:
            return 401
        if code in forbidden_codes:
            return 403
        if code in not_found_codes:
            return 404
        if code in unsupported_codes:
            return 415
        if code in rate_limit_codes:
            return 429
        if code in timeout_codes:
            return 504
        if code in not_implemented_codes:
            return 501
        return 500
