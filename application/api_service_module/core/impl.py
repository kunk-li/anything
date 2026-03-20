# -*- coding: utf-8 -*-
"""
API 服务模块具体实现类
负责 HTTP 协议层处理、鉴权、中间件、trace_id 透传与统一错误映射
"""

import json
import time
import uuid
from typing import Dict, Any, Optional

from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse

from common_utils_module.core.impl import CommonUtils
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import ExceptionHandler


class ApiService:
    """标准 API 服务实现：只负责 HTTP 协议层，不承载业务语义校验"""

    def __init__(self, handler):
        self.utils = CommonUtils()
        self.logger = SystemLogger()
        self.config = ConfigManager()
        if hasattr(self.config, "load_config"):
            self.config.load_config()
        elif hasattr(self.config, "load"):
            self.config.load()
        self.exception_handler = ExceptionHandler()

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

        self._register_middlewares()
        self._register_routes()
        self._register_exception_handlers()

        self.logger.info("API 服务模块初始化完成")

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
            headers = {"X-Request-Id": trace_id}
            headers["X-Cost-Time"] = str(round(time.time() - start_time, 3))

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
