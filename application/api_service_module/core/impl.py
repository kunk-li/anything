# -*- coding: utf-8 -*-
"""
API 服务模块具体实现类
负责 HTTP 协议层处理、鉴权、中间件、trace_id 透传与统一错误映射
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional

import asyncio

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from deps_module import BasicDeps, build_basic_deps, StartupError
from observability_module import (
    trace_span,
    set_current_tenant,
    reset_current_tenant,
)


class ApiService:
    """标准 API 服务实现：只负责 HTTP 协议层，不承载业务语义校验"""

    def __init__(
        self,
        handler,
        deps: Optional[BasicDeps] = None,
        document_store_factory=None,
        llm_service=None,
    ):
        """
        Args:
            document_store_factory: Callable[[str tenant_id], doc_store_instance]
                给 GET /documents/{doc_id}/preview 用 — 按租户动态构造 LocalDocumentStore。
                不传时该端点返回 SERVICE_UNAVAILABLE (前端 chunk 跳转预览功能降级)。
            llm_service: LLMService 实例, 给 /config/models 系列端点 (运行期注册/编辑 LLM)。
                不传时这组端点返回 SERVICE_UNAVAILABLE。
                ⚠️ 这组端点能编辑 api_key, 生产部署需在反代/网关加 admin 鉴权,
                  本期所有认证用户都能改 (跟 /metrics 同等权限);
                  对外 SaaS 场景请在网关把 /config/* 屏蔽掉。
        """
        self.document_store_factory = document_store_factory
        self.llm_service = llm_service
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
        # api_keys 支持两种格式 (Task #33 PR2, 详见 docs/multi-tenancy-design.md §5.2-5.3):
        #   - list (老格式): ["k1", "k2"] -> 全部映射到 tenant="default" + WARN
        #   - dict (新格式): {"tenant-a": ["k1"], "default": ["k2"]} -> 反向映射 + 唯一性校验
        # 内存维护 _key_to_tenant: Dict[str, str] 反向索引, 认证 O(1) 查表
        raw_api_keys = self.config.get_config("security.api_keys", []) or []
        self._key_to_tenant: Dict[str, str] = self._build_key_to_tenant_index(raw_api_keys)
        # 兼容: 老代码若直接读 self.api_keys 看是否在列表内, 仍能工作
        self.api_keys = list(self._key_to_tenant.keys())
        # 内部白名单 (本期占位, 真实使用在 §4.3 冲突处理): 配置 internal_whitelist 后, 这些 IP 可仅靠 body tenant_id
        self.internal_whitelist: List[str] = list(
            self.config.get_config("security.internal_whitelist", []) or []
        )
        self.max_body_size = int(self.config.get_config("api_service.max_body_size", 1048576))
        self.enable_health_details = bool(
            self.config.get_config("api_service.enable_health_details", True)
        )

        # Metrics cardinality 守护 (Task #33 PR4a, 见 docs/multi-tenancy-design.md §7.2)
        # 启动期把 api_keys 中所有合法租户加入 allowlist; 超过 top_n 后只保留 top_n 个,
        # 其余请求 metrics 标签使用 tenant="other" 聚合, 防止 Prometheus 时间序列爆炸。
        self.metrics_tenant_label_top_n = int(
            self.config.get_config("observability.metrics_tenant_label_top_n", 500)
        )
        all_tenants = set(self._key_to_tenant.values()) | {"default"}
        if len(all_tenants) > self.metrics_tenant_label_top_n:
            # 取前 top_n 个(字典序稳定;运行期实际"top by traffic"留作后续优化)
            self._tenant_label_allowlist = set(
                sorted(all_tenants)[: self.metrics_tenant_label_top_n]
            )
            self.logger.info(
                f"[observability] tenant cardinality={len(all_tenants)}, "
                f"threshold={self.metrics_tenant_label_top_n}, using top-N strategy"
            )
        else:
            self._tenant_label_allowlist = all_tenants

        # 简单的 in-memory metrics, 通过 /metrics 端点暴露为 Prometheus 文本格式
        # (单进程跑 uvicorn 够用; 多 worker 需后续接 prometheus_client + multiprocess mode)
        # PR4a: 把 type 单键改为 (type, tenant) 复合键, errors 也带 tenant 维度。
        self._metrics_lock = threading.Lock()
        self._metrics: Dict[str, Dict[Any, float]] = {
            "requests_by_type": {},        # (type, tenant) -> count
            "errors_by_code": {},          # (code, tenant) -> count
            "duration_sum_by_type": {},    # (type, tenant) -> sum(seconds)
            "duration_count_by_type": {},  # (type, tenant) -> count
        }

        # PR4b: 配额体系 — 已知 tenant 集合 + per-tenant QPS 滑窗
        # known_tenants = api_keys 配置出现的 + quotas 配置出现的 + 'default'
        # 未在此集合中, 但 body 显式声明的 tenant_id -> 404 TENANT_NOT_FOUND
        quota_tids = set(
            (self.config.get_config("quotas", {}) or {}).keys()
        )
        # 移除非 tenant 性质的全局键(如 tenant_deletion_grace_hours 见 §9.4.1)
        quota_tids = {t for t in quota_tids if isinstance(t, str) and t != "tenant_deletion_grace_hours"}
        self._known_tenants = set(self._key_to_tenant.values()) | quota_tids | {"default"}

        # QPS 滑窗: per-tenant deque[float timestamp], 窗口 1s, max_qps 阈值从 quotas 取
        from collections import deque as _deque
        self._qps_lock = threading.Lock()
        self._qps_windows: Dict[str, "_deque[float]"] = {}

        self._register_middlewares()
        self._register_routes()
        self._register_exception_handlers()

        self.logger.info("API 服务模块初始化完成")

    def _is_known_tenant(self, tenant_id: str) -> bool:
        """PR4b: tenant_id 是否在已知集合中.

        未知 tenant_id (即便格式合法) -> ApiService 返回 404 TENANT_NOT_FOUND,
        防租户枚举攻击 (§9.3 统一不区分"存在但无权"与"不存在")。
        """
        return tenant_id in self._known_tenants

    def _check_qps_quota(self, tenant_id: str) -> bool:
        """PR4b QPS 滑动窗口检查.

        策略: per-tenant 1 秒窗口 deque[timestamp], 新请求来时:
            1. 弹掉窗外的旧 timestamp
            2. 若剩余数 >= max_qps 阈值, 拒绝并 ERROR 日志
            3. 否则 push 新 timestamp

        返回:
            True 表示放行, False 表示被限流 (调用方应返回 429 API_RATE_LIMITED)。
        """
        try:
            max_qps = self.config.get_config(f"quotas.{tenant_id}.max_qps", None)
        except Exception:
            max_qps = None
        if max_qps is None:
            return True
        try:
            max_qps_n = int(max_qps)
        except (TypeError, ValueError):
            return True
        if max_qps_n <= 0:
            return True

        from collections import deque as _deque
        now = time.time()
        with self._qps_lock:
            window = self._qps_windows.get(tenant_id)
            if window is None:
                window = _deque()
                self._qps_windows[tenant_id] = window
            # 弹出 1s 窗外的
            while window and (now - window[0]) > 1.0:
                window.popleft()
            if len(window) >= max_qps_n:
                self.logger.error(
                    f"[quota] QPS rate limit exceeded: tenant={tenant_id} "
                    f"current_window={len(window)} max_qps={max_qps_n}"
                )
                return False
            window.append(now)
        return True

    def _bucket_tenant(self, tenant_id: Optional[str]) -> str:
        """租户 cardinality 守护: allowlist 之外的全部聚合为 "other".

        见 docs/multi-tenancy-design.md §7.2 — 防 Prometheus 时间序列爆炸。
        """
        tid = (tenant_id or "default") or "default"
        if tid in self._tenant_label_allowlist:
            return tid
        return "other"

    def _record_metrics(
        self,
        req_type: str,
        code: str,
        duration: float,
        tenant_id: Optional[str] = None,
    ) -> None:
        """更新 metrics 计数. 持锁是因为 FastAPI 可能并发处理多请求.

        PR4a: 加入 tenant 标签维度, 用 (type, tenant) / (code, tenant) 元组作为 key,
        在渲染时拆开为 Prometheus 多标签。
        """
        with self._metrics_lock:
            req_type = req_type or "unknown"
            tenant = self._bucket_tenant(tenant_id)
            key_t = (req_type, tenant)
            self._metrics["requests_by_type"][key_t] = (
                self._metrics["requests_by_type"].get(key_t, 0) + 1
            )
            if code != "SUCCESS":
                key_e = (code, tenant)
                self._metrics["errors_by_code"][key_e] = (
                    self._metrics["errors_by_code"].get(key_e, 0) + 1
                )
            self._metrics["duration_sum_by_type"][key_t] = (
                self._metrics["duration_sum_by_type"].get(key_t, 0.0) + duration
            )
            self._metrics["duration_count_by_type"][key_t] = (
                self._metrics["duration_count_by_type"].get(key_t, 0) + 1
            )

    def _render_prometheus_metrics(self) -> str:
        """把 in-memory metrics 渲染为 Prometheus 文本格式.

        PR4a: 输出多标签 — type + tenant; errors_total 增加 tenant 标签。
        """
        with self._metrics_lock:
            snapshot = {
                k: dict(v) for k, v in self._metrics.items()
            }

        lines = []

        lines.append("# HELP anything_requests_total Total RAG/Agent requests handled")
        lines.append("# TYPE anything_requests_total counter")
        for (t, tenant), n in snapshot["requests_by_type"].items():
            lines.append(
                f'anything_requests_total{{type="{t}",tenant="{tenant}"}} {int(n)}'
            )

        lines.append("")
        lines.append("# HELP anything_errors_total Total non-SUCCESS responses by code")
        lines.append("# TYPE anything_errors_total counter")
        for (code, tenant), n in snapshot["errors_by_code"].items():
            lines.append(
                f'anything_errors_total{{code="{code}",tenant="{tenant}"}} {int(n)}'
            )

        lines.append("")
        lines.append("# HELP anything_request_duration_seconds_sum Cumulative request duration")
        lines.append("# TYPE anything_request_duration_seconds_sum counter")
        for (t, tenant), s in snapshot["duration_sum_by_type"].items():
            lines.append(
                f'anything_request_duration_seconds_sum{{type="{t}",tenant="{tenant}"}} {s:.6f}'
            )

        lines.append("")
        lines.append("# HELP anything_request_duration_seconds_count Total samples for duration")
        lines.append("# TYPE anything_request_duration_seconds_count counter")
        for (t, tenant), n in snapshot["duration_count_by_type"].items():
            lines.append(
                f'anything_request_duration_seconds_count{{type="{t}",tenant="{tenant}"}} {int(n)}'
            )

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

            # 3.5 tenant_id 冲突处理 (Task #33 PR2, 见 docs/multi-tenancy-design.md §4.3)
            #     auth_tenant_id (认证产物) 优先于 body tenant_id (声明)
            self._reconcile_tenant_id(request, body, trace_id)

            # PR4a (§7.3.1): 把 reconcile 后的 tenant_id 注入 ContextVar,
            # 让所有 trace_span / 业务日志能自动拿到. body 没声明就用 default。
            effective_tid = str(body.get("tenant_id") or "default")

            # PR4b: 未知 tenant -> 404 TENANT_NOT_FOUND (§9.3 防枚举, 跟 DOCUMENT_NOT_FOUND 同桶)
            if not self._is_known_tenant(effective_tid):
                self.logger.warning(
                    f"[security] unknown tenant_id={effective_tid!r} trace_id={trace_id}"
                )
                duration = time.time() - start_time
                self._record_metrics(
                    req_type=str(body.get("type", "unknown")),
                    code="TENANT_NOT_FOUND",
                    duration=duration,
                    tenant_id=effective_tid,
                )
                return JSONResponse(
                    status_code=404,
                    content={
                        "code": "TENANT_NOT_FOUND",
                        "message": "tenant 不存在或无权访问",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,  # §9.3: 不暴露存在性
                    },
                    headers={"X-Request-Id": trace_id},
                )

            # PR4b: per-tenant QPS 滑窗限流 (§8 沿用 API_RATE_LIMITED 429)
            if not self._check_qps_quota(effective_tid):
                duration = time.time() - start_time
                self._record_metrics(
                    req_type=str(body.get("type", "unknown")),
                    code="API_RATE_LIMITED",
                    duration=duration,
                    tenant_id=effective_tid,
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "code": "API_RATE_LIMITED",
                        "message": "请求频率超出租户配额, 请稍后重试",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": True,
                        "details": {"tenant_id": effective_tid},
                    },
                    headers={"X-Request-Id": trace_id, "Retry-After": "1"},
                )

            tenant_token = set_current_tenant(effective_tid)
            try:
                # 4. 透传到接口层 (OTel root span 包住整个请求)
                with trace_span(
                    "api.invoke",
                    attributes={
                        "http.method": "POST",
                        "http.route": "/invoke",
                        "anything.trace_id": trace_id,
                        "anything.request_type": str(body.get("type", "unknown")),
                    },
                ) as span:
                    result = self.handler.handle(body, trace_id=trace_id)
                    span.set_attribute(
                        "anything.response_code", str(result.get("code", "UNKNOWN_ERROR"))
                    )

                # 5. 应用层只负责业务码 -> HTTP 状态码映射
                http_status = self._map_code_to_http_status(result.get("code", "UNKNOWN_ERROR"))
                duration = time.time() - start_time
                headers = {"X-Request-Id": trace_id}
                headers["X-Cost-Time"] = str(round(duration, 3))

                # 6. 记录 metrics (异步级别开销极低), 带 tenant 维度 + cardinality 守护
                self._record_metrics(
                    req_type=str(body.get("type", "unknown")),
                    code=str(result.get("code", "UNKNOWN_ERROR")),
                    duration=duration,
                    tenant_id=effective_tid,
                )

                return JSONResponse(
                    status_code=http_status,
                    content=result,
                    headers=headers,
                )
            finally:
                # 无论成功 / 异常, 都必须 reset ContextVar, 防泄漏到下个请求
                reset_current_tenant(tenant_token)

        @self.app.websocket("/invoke/stream")
        async def invoke_stream(ws: WebSocket):
            """WebSocket 流式回答端点 (V1: 简化版 — 后端先拿完整答案再切片发).

            协议:
                Client -> Server (一次性 send_json):
                    RequestEnvelope (同 POST /invoke), 可带 X-API-Key
                    认证头通过 query string 'api_key' 或子协议传递 (浏览器 ws 不支持自定义 header)
                Server -> Client (流式 send_json):
                    {type: 'start',     trace_id, tenant_id}
                    {type: 'chunk',     text}              # 重复 N 次
                    {type: 'metadata',  citations, retrieved_chunks, steps}
                    {type: 'done',      code, cost_time, trace_id}
                    {type: 'error',     code, message}    # 出错时替代 done

            限制 (V1 / TODO 改造为真实 LLM 流式):
                - 实际是同步跑完 handler.handle 后才切片, total 延迟跟非流式一致
                - 当 LLMService 升级到 token stream 后, 这里把 handler 换成 handler.handle_stream 即可

            参考: docs/multi-tenancy-design.md §7.3 OTel 在 WS 上的传播本期不实现
                  (浏览器 WS API 没自定义 header, 后续需要换 querystring 模式)
            """
            # 鉴权 (querystring 风格, 因为浏览器 WS 不能自定义 header)
            api_key_qs = ws.query_params.get("api_key", "")
            if self.auth_enabled and self.auth_type == "apikey":
                if not api_key_qs or api_key_qs not in self._key_to_tenant:
                    await ws.close(code=4401, reason="AUTH_REQUIRED")
                    return

            await ws.accept()
            trace_id = ws.headers.get("X-Request-Id") or self._generate_trace_id()
            tenant_token = None
            try:
                # 1. 收一条请求消息
                try:
                    body = await ws.receive_json()
                except Exception:
                    await ws.send_json({
                        "type": "error", "code": "BAD_REQUEST",
                        "message": "首条消息必须是 JSON object",
                        "trace_id": trace_id,
                    })
                    return

                if not isinstance(body, dict):
                    await ws.send_json({
                        "type": "error", "code": "BAD_REQUEST",
                        "message": "请求体必须是 JSON object",
                        "trace_id": trace_id,
                    })
                    return

                # 2. tenant reconcile — 复用 _resolve_tenant_from_auth 但走 ws.query_params
                auth_tid = self._key_to_tenant.get(api_key_qs) if api_key_qs else None
                body_tid = body.get("tenant_id")
                if auth_tid:
                    body["tenant_id"] = auth_tid
                # internal IP check: ws.client.host
                client_host = ws.client.host if ws.client else ""
                is_internal = any(
                    str(e).split("/")[0] and client_host.startswith(str(e).split("/")[0].rstrip("."))
                    for e in self.internal_whitelist
                )
                if not auth_tid and body_tid and not is_internal:
                    body.pop("tenant_id", None)

                effective_tid = str(body.get("tenant_id") or "default")
                if not self._is_known_tenant(effective_tid):
                    await ws.send_json({
                        "type": "error", "code": "TENANT_NOT_FOUND",
                        "message": "tenant 不存在或无权访问",
                        "trace_id": trace_id,
                    })
                    return

                # QPS quota
                if not self._check_qps_quota(effective_tid):
                    await ws.send_json({
                        "type": "error", "code": "API_RATE_LIMITED",
                        "message": "请求频率超出租户配额",
                        "trace_id": trace_id,
                    })
                    return

                tenant_token = set_current_tenant(effective_tid)

                # 3. 通知客户端"开始"
                await ws.send_json({
                    "type": "start",
                    "trace_id": trace_id,
                    "tenant_id": effective_tid,
                    "request_type": body.get("type", "unknown"),
                })

                # 4. 同步跑 handler (V1: 不是真实 token 流, 跑完再切片)
                start_t = time.time()
                # FastAPI 的 ws handler 是 async; handler.handle 是 sync.
                # 把它跑到 thread pool 避免阻塞 event loop。
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, lambda: self.handler.handle(body, trace_id=trace_id)
                )
                duration = time.time() - start_t

                code = str(result.get("code", "UNKNOWN_ERROR"))
                if code != "SUCCESS":
                    await ws.send_json({
                        "type": "error",
                        "code": code,
                        "message": result.get("message", ""),
                        "trace_id": trace_id,
                        "retryable": bool(result.get("retryable")),
                        "details": result.get("details"),
                    })
                    self._record_metrics(
                        req_type=str(body.get("type", "unknown")),
                        code=code,
                        duration=duration,
                        tenant_id=effective_tid,
                    )
                    return

                # 5. 切片发送 answer (按 ~20 char 一片, 模拟流式)
                data = result.get("data") or {}
                answer = str(data.get("answer") or "")
                chunk_size = 20
                for i in range(0, len(answer), chunk_size):
                    await ws.send_json({"type": "chunk", "text": answer[i:i + chunk_size]})
                    # 5ms 间隔, 让 UI 有"打字感";真实 LLM 流式后这里直接跟 generator
                    await asyncio.sleep(0.005)

                # 6. 发 metadata (citations / chunks / steps)
                meta_payload = {
                    "type": "metadata",
                    "citations": data.get("citations") or [],
                    "retrieved_chunks": data.get("retrieved_chunks") or [],
                    "steps": data.get("steps") or [],
                }
                await ws.send_json(meta_payload)

                # 7. 完成
                await ws.send_json({
                    "type": "done",
                    "code": "SUCCESS",
                    "cost_time": round(duration, 3),
                    "trace_id": trace_id,
                })

                self._record_metrics(
                    req_type=str(body.get("type", "unknown")),
                    code="SUCCESS",
                    duration=duration,
                    tenant_id=effective_tid,
                )
            except WebSocketDisconnect:
                # 客户端主动断开, 静默
                pass
            except Exception as e:
                self.logger.error(f"[ws] /invoke/stream 异常: {e}", exc_info=True)
                try:
                    await ws.send_json({
                        "type": "error", "code": "UNKNOWN_ERROR",
                        "message": str(e), "trace_id": trace_id,
                    })
                except Exception:
                    pass
            finally:
                if tenant_token is not None:
                    reset_current_tenant(tenant_token)
                try:
                    await ws.close()
                except Exception:
                    pass

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

        # ============== LLM 模型管理 (运行期注册表) ==============
        # 客户端能在 Web UI 里编辑模型 + key, 不持久化到 yaml (重启丢失)。
        # ⚠️ 生产部署在网关层屏蔽 /config/* (除非你已加 admin RBAC)。

        def _need_llm_service():
            if self.llm_service is None:
                return JSONResponse(
                    status_code=501,
                    content={
                        "code": "SERVICE_UNAVAILABLE",
                        "message": "llm_service 未注入, 模型管理端点不可用",
                        "data": None,
                        "trace_id": None,
                        "retryable": False,
                        "details": None,
                    },
                )
            return None

        @self.app.get("/config/models")
        async def list_models(request: Request):
            err = _need_llm_service()
            if err is not None:
                return err
            trace_id = request.state.trace_id
            try:
                models = self.llm_service.list_models(mask_keys=True)
            except Exception as e:
                self.logger.error(f"list_models failed: {e}", exc_info=True)
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
                    "data": {"models": models},
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.post("/config/models")
        async def register_model(request: Request):
            err = _need_llm_service()
            if err is not None:
                return err
            trace_id = request.state.trace_id
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "BAD_REQUEST",
                        "message": "请求体不是合法 JSON",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )

            name = (body.get("name") or "").strip()
            request_type = (body.get("request_type") or "").upper()
            adapter_class = (body.get("adapter_class") or "").strip()
            api_key = body.get("api_key") or ""
            api_base = (body.get("api_base") or "").strip()
            set_as_default = bool(body.get("set_as_default", False))

            if not name or not request_type or not adapter_class:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "PARAM_MISSING",
                        "message": "name / request_type / adapter_class 必填",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": {
                            "name": bool(name),
                            "request_type": bool(request_type),
                            "adapter_class": bool(adapter_class),
                        },
                    },
                    headers={"X-Request-Id": trace_id},
                )
            try:
                entry = self.llm_service.register_or_update_model(
                    name=name,
                    request_type=request_type,
                    adapter_class=adapter_class,
                    api_key=api_key,
                    api_base=api_base,
                    set_as_default=set_as_default,
                )
            except ValueError as ve:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "PARAM_INVALID",
                        "message": str(ve),
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )
            except Exception as e:
                self.logger.error(f"register_model failed: {e}", exc_info=True)
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
                    "message": "model registered",
                    "data": entry,
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.delete("/config/models/{name}")
        async def delete_model(name: str, request: Request):
            err = _need_llm_service()
            if err is not None:
                return err
            trace_id = request.state.trace_id
            existed = self.llm_service.unregister_model(name)
            return JSONResponse(
                status_code=200 if existed else 404,
                content={
                    "code": "SUCCESS" if existed else "MODEL_NOT_FOUND",
                    "message": "removed" if existed else "model 不存在",
                    "data": {"name": name, "existed": existed},
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.post("/config/models/{name}/set-default")
        async def set_default_model(name: str, request: Request):
            err = _need_llm_service()
            if err is not None:
                return err
            trace_id = request.state.trace_id
            try:
                body = await request.json()
            except Exception:
                body = {}
            request_type = (body.get("request_type") or "") if isinstance(body, dict) else ""
            try:
                result = self.llm_service.set_default_model(name=name, request_type=request_type)
            except ValueError as ve:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "PARAM_INVALID",
                        "message": str(ve),
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
                    "message": "default updated",
                    "data": result,
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        # ============== 前端 Web UI 挂载 ==============
        # frontend/ 目录在项目根: <repo_root>/frontend/{index.html, static/*}
        # 服务启动 cwd 不固定 (可能在 run/ 也可能在 repo root), 用 __file__ 反推。
        # 找不到时不挂载, 不影响纯 API 部署。
        self._mount_frontend()

    def _mount_frontend(self) -> None:
        """挂载 frontend/ 静态资源 + GET / 返回 index.html.

        路径解析: <api_service_module/core/impl.py>
            -> ../../../../frontend/      (从 application/api_service_module/core/ 上溯 4 层)
            -> or <cwd>/frontend/         (兜底)
            -> or <cwd>/../frontend/      (在 run/ 下启动时)

        找不到 index.html 时跳过挂载并 INFO 日志, 不挂掉服务。
        """
        candidates = [
            Path(__file__).resolve().parents[3] / "frontend",
            Path.cwd() / "frontend",
            Path.cwd().parent / "frontend",
        ]
        frontend_dir = next((p for p in candidates if (p / "index.html").exists()), None)
        if frontend_dir is None:
            self.logger.info(
                "未找到 frontend/index.html, 跳过 Web UI 挂载 (纯 API 模式)"
            )
            return

        static_dir = frontend_dir / "static"
        if static_dir.is_dir():
            self.app.mount(
                "/static", StaticFiles(directory=str(static_dir)), name="static"
            )

        index_path = frontend_dir / "index.html"

        @self.app.get("/")
        async def _root():
            return FileResponse(str(index_path), media_type="text/html; charset=utf-8")

        # 也支持 /ui 别名 (有些反代会在 / 挂别的内容)
        @self.app.get("/ui")
        async def _ui():
            return FileResponse(str(index_path), media_type="text/html; charset=utf-8")

        self.logger.info(f"Web UI 已挂载: GET / 提供 index.html, 静态资源 from {static_dir}")

    # =========================
    # 辅助方法
    # =========================
    def _reconcile_tenant_id(self, request: Request, body: Dict[str, Any], trace_id: str) -> None:
        """处理 auth_tenant_id 与 body['tenant_id'] 的冲突。

        规则(见 docs/multi-tenancy-design.md §4.3):
            - 仅 auth_tenant_id 存在 -> 用 auth
            - 仅 body 存在(认证未携带): 仅 internal IP 允许;否则保留 body(后续 RequestHandler 补 default)
            - 两者一致 -> 用 auth (等价)
            - 两者不一致 -> 用 auth + 记 ERROR (疑似越权)

        本方法直接修改 body 字典, body['tenant_id'] 最终为认证产物或保留原值。
        """
        auth_tid = self._resolve_tenant_from_auth(request)
        body_tid = body.get("tenant_id")

        if auth_tid:
            if body_tid and body_tid != auth_tid:
                self.logger.error(
                    f"[security] tenant_id mismatch: auth={auth_tid!r} body={body_tid!r} "
                    f"trace_id={trace_id} -- 疑似越权尝试, 强制使用认证产物"
                )
                # metrics counter (本期借用 errors_by_code, 后续可单独记 anything_tenant_mismatch_total)
            body["tenant_id"] = auth_tid
            return

        # 没有认证产物: 若 body 显式声明 tenant_id, 仅 internal IP 允许保留
        if body_tid and not self._is_internal_ip(request):
            self.logger.warning(
                f"[security] body 声明 tenant_id={body_tid!r} 但请求未认证且非 internal IP, "
                f"忽略 body 声明 (后续 RequestHandler 会补 default), trace_id={trace_id}"
            )
            body.pop("tenant_id", None)
        # else: 内部 IP 保留 body['tenant_id'], 或 body 本来就没声明 -> 不动

    def _build_key_to_tenant_index(self, raw: Any) -> Dict[str, str]:
        """从 yaml security.api_keys 构造 key -> tenant_id 反向索引。

        支持两种输入格式:
            - list ["k1", "k2"] (老格式, 全部映射到 default + 启动 WARN)
            - dict {"tenant-a": ["k1"], "default": ["k2"]} (新格式)
              一个 key 严格只能绑一个 tenant_id (决议 3); 多绑触发 StartupError

        见 docs/multi-tenancy-design.md §5.3
        """
        if isinstance(raw, list):
            if raw:
                self.logger.warning(
                    f"[security] detected legacy api_keys list format; "
                    f"all {len(raw)} keys mapped to tenant='default'. "
                    f"Multi-tenancy disabled. Migrate to tenant->keys dict to enable."
                )
            return {str(k): "default" for k in raw if k}

        if isinstance(raw, dict):
            seen: Dict[str, str] = {}
            for tid, keys in raw.items():
                if not isinstance(keys, list):
                    raise StartupError(
                        component="security.api_keys",
                        reason=f"tenant '{tid}' 的 keys 应是 list, 实际是 {type(keys).__name__}",
                        hint="格式: tenant_id: [\"key1\", \"key2\"]",
                    )
                for key in keys:
                    key_str = str(key) if key is not None else ""
                    if not key_str:
                        continue
                    if key_str in seen and seen[key_str] != tid:
                        raise StartupError(
                            component="security.api_keys",
                            reason=(
                                f"API key bound to multiple tenants: "
                                f"'{tid}' vs '{seen[key_str]}'"
                            ),
                            hint="一个 API key 只能绑一个 tenant_id;跨租户访问请发多个 key",
                        )
                    seen[key_str] = tid
            return seen

        # 配置类型异常 -> 空映射 (等价于无鉴权配置)
        if raw not in (None, [], {}):
            self.logger.warning(
                f"[security] api_keys 配置类型不支持: {type(raw).__name__}; 视为空映射"
            )
        return {}

    def _resolve_tenant_from_auth(self, request: Request) -> Optional[str]:
        """从认证产物提取 tenant_id。

        - apikey: 查 _key_to_tenant 反向映射
        - jwt: 解析 tenant_id claim (本期 TODO, 留作 stub)
        - none: 返回 None
        """
        if not self.auth_enabled or self.auth_type == "none":
            return None
        if self.auth_type == "apikey":
            api_key = request.headers.get("X-API-Key")
            if api_key:
                return self._key_to_tenant.get(api_key)
        # 其他认证方式占位
        return None

    def _is_internal_ip(self, request: Request) -> bool:
        """判断请求源 IP 是否在 internal_whitelist 中 (§4.3 冲突处理用)。

        本期实现为字符串前缀匹配 (简单, 够用); 后续可换 ipaddress.IPv4Network。
        """
        if not self.internal_whitelist:
            return False
        client = request.client.host if request.client else ""
        if not client:
            return False
        for entry in self.internal_whitelist:
            # 支持 "127.0.0.1" 或 "10.0.0.0/8" -> 简单前缀匹配
            base = str(entry).split("/")[0]
            if base and client.startswith(base.rstrip(".")):
                return True
        return False

    def _check_auth(self, request: Request, trace_id: str) -> Optional[JSONResponse]:
        """鉴权检查:仅处理协议层鉴权,不涉及业务逻辑。

        成功 -> 返回 None (调用方继续, tenant_id 通过 _resolve_tenant_from_auth 读取)
        失败 -> 返回 JSONResponse (401)
        """
        if not self.auth_enabled or self.auth_type == "none":
            return None

        if self.auth_type == "apikey":
            api_key = request.headers.get("X-API-Key")
            if not api_key or api_key not in self._key_to_tenant:
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
        # PR4b: TENANT_REQUIRED 401 (未携带合法 tenant 或被停用), 与 AUTH_REQUIRED 同语义
        unauthorized_codes = {"AUTH_REQUIRED", "TENANT_REQUIRED"}
        forbidden_codes = {"AUTH_FORBIDDEN"}
        # PR4b: TENANT_NOT_FOUND 404 (§9.3 防租户枚举, 跟 DOCUMENT_NOT_FOUND 同桶)
        not_found_codes = {
            "DOCUMENT_NOT_FOUND", "FOLDER_NOT_FOUND", "STATE_NOT_FOUND", "TENANT_NOT_FOUND",
        }
        unsupported_codes = {"UNSUPPORTED_FILE_TYPE"}
        # PR4b: QUOTA_* 429, retryable (复用现有 API_RATE_LIMITED 桶)
        rate_limit_codes = {
            "API_RATE_LIMITED", "QUOTA_DOC_EXCEEDED", "QUOTA_STORAGE_EXCEEDED",
        }
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
