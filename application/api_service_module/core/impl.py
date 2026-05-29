# -*- coding: utf-8 -*-
"""
API 服务模块具体实现类
负责 HTTP 协议层处理、鉴权、中间件、trace_id 透传与统一错误映射
"""

import json
import os
import threading
import time
import traceback
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


from .routers import (
    InvokeRoutesMixin,
    DocumentsRoutesMixin,
    IndexRoutesMixin,
    AdminRoutesMixin,
    ConfigRoutesMixin,
    MemoryRoutesMixin,
    SchedulerRoutesMixin,
    SessionsRoutesMixin,
    AgentRoutesMixin,  # Task FFFF (#123)
    FrontendRoutesMixin,
)


class ApiService(
    InvokeRoutesMixin,
    DocumentsRoutesMixin,
    IndexRoutesMixin,
    AdminRoutesMixin,
    ConfigRoutesMixin,
    MemoryRoutesMixin,
    SchedulerRoutesMixin,
    SessionsRoutesMixin,
    AgentRoutesMixin,  # Task FFFF (#123)
    FrontendRoutesMixin,
):
    """标准 API 服务实现: 只负责 HTTP 协议层, 不承载业务语义校验.

    Task LL (#72): 21 个路由按域拆到 routers/ 下 6 个 mixin (Invoke/Documents/
        Index/Admin/Config/Frontend). 公共 API 不变, 测试 0 改动.
    Task GGG (#93): 加 MemoryRoutesMixin /memory/* 5 个路由.
    """

    def __init__(
        self,
        handler,
        deps: Optional[BasicDeps] = None,
        document_store_factory=None,
        llm_service=None,
        index_runner=None,
        rag_runner=None,           # Task S: 读 hybrid 开关 / bm25_retriever 状态
        vector_db=None,            # Task S: 读 ntotal
        long_term_memory=None,     # Task GGG (#93): /memory/* 路由用
        scheduler=None,            # Task PPP (#102): /scheduler/* 路由用 (II #69 TaskScheduler)
        state_store=None,          # Task SSS (#105): /sessions/* 路由用
        tool_registry=None,        # Task FFFF (#123): /agent/tools 路由用
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
        # index_runner: Callable[[str file_path], Dict[str,Any]]
        # 给 /documents/upload 用 — 上传后立刻跑 parse + chunk + embed + upsert,
        # 让 RAG 检索能立即查到。不传时上传只落盘不索引 (老行为)。
        self.index_runner = index_runner
        # Task S: 给 /admin/status 用 — 透出 hybrid 开关 / BM25 size / vector_db ntotal
        self.rag_runner = rag_runner
        self.vector_db = vector_db
        # Task GGG (#93): /memory/* 路由用. None 时所有 memory 路由返 SERVICE_UNAVAILABLE.
        self.long_term_memory = long_term_memory
        # Task PPP (#102): /scheduler/* 路由用. None 时所有 scheduler 路由返 SERVICE_UNAVAILABLE.
        self.scheduler = scheduler
        # Task SSS (#105): /sessions/* 路由用. 注入 state_store 让用户能列/删 session.
        self.state_store = state_store
        # Task FFFF (#123): /agent/tools 路由用. 注入 tool_registry 让前端能列工具.
        self.tool_registry = tool_registry
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

        # Dev 友好: DEV_MODE 启动 + 全部 api_keys 是占位/默认 sentinel 时, 自动关掉认证.
        # 让新人 clone → run → 浏览器即用, 不强迫第一次配 key.
        # 真实部署 (export API_KEY_1=xxx + 关 DEV_MODE) 不受影响.
        #
        # 关闭条件 (任一即关):
        #   1. 没配 api_keys
        #   2. 全是未解析 ${ENV} 占位符  (e.g. 用户没建 .env)
        #   3. 全是 dev sentinel 默认值 (key 带 "_change_in_prod" 后缀, 跟 .env.example 一致)
        #
        # Task BBBB 续: 加 sentinel 检测 — .env 从 .env.example 复制后默认值就是 dev sentinel,
        # 这种情况下应该和"没配"等价, 否则用户复制了 .env 就被强制 auth 不直观.
        dev_mode = os.environ.get("ANYTHING_DEV_MODE", "").lower() in ("1", "true", "yes")
        all_placeholders = bool(self._key_to_tenant) and all(
            k.startswith("${") and k.endswith("}") for k in self._key_to_tenant
        )
        all_dev_sentinels = bool(self._key_to_tenant) and all(
            "_change_in_prod" in k for k in self._key_to_tenant
        )
        if dev_mode and self.auth_enabled and self.auth_type == "apikey" and (
            not self._key_to_tenant or all_placeholders or all_dev_sentinels
        ):
            if not self._key_to_tenant:
                reason = "未配置 api_keys"
            elif all_placeholders:
                reason = "api_keys 全是未解析 ${ENV} 占位符"
            else:
                reason = "api_keys 全是 dev sentinel 默认值 (含 _change_in_prod)"
            self.logger.warning(
                f"[security] DEV_MODE 检测到 {reason}, 自动关闭 auth。"
                f"生产部署请: (1) export 真实 API_KEY_1 环境变量 (2) 不要设 ANYTHING_DEV_MODE。"
            )
            self.auth_enabled = False
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
            self.logger.error(
                f"全局异常: trace_id={trace_id}, error={exc}\n{traceback.format_exc()}"
            )
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
    # =========================
    # 路由注册 (Task LL #72: 按域拆到 routers/ 下 6 个 mixin)
    # =========================
    def _register_routes(self) -> None:
        self._register_invoke_routes()
        self._register_documents_routes()
        self._register_index_routes()
        self._register_admin_routes()
        self._register_config_routes()
        self._register_memory_routes()  # Task GGG (#93): /memory/* 5 路由
        self._register_scheduler_routes()  # Task PPP (#102): /scheduler/* 3 路由
        self._register_sessions_routes()  # Task SSS (#105): /sessions/* 3 路由
        self._register_agent_routes()  # Task FFFF (#123): /agent/tools 1 路由
        # Task VV (#82): 在前端路由之前, 把所有现有 API 路由复制一份到 /v1/<path>
        # 让客户端可逐步迁到带版本的 URL; 老 path 保留无限期 (打 deprecated 头).
        self._register_v1_aliases()
        # 前端路由最后注册, 避免覆盖 /admin/status 等 GET 端点
        self._register_frontend_routes()

    def _register_v1_aliases(self) -> None:
        """Task VV (#82): 给所有 API 路由加 /v1/<path> 镜像别名.

        遍历 self.app.routes 快照, 对每个 path 不以 /v1/ 开头且不属于 frontend/health
        排除清单的, 用同一个 endpoint 函数再 register 一份带 /v1 前缀的路由.

        排除清单 (这些 path 不加版本前缀):
            /              SPA 入口
            /ui            SPA 别名
            /static/*      静态资源 (Mount, 不在 self.app.routes 里)
            /health        k8s liveness probe 习惯打不带版本的
            /healthz       同上
            /openapi.json  FastAPI 自动暴露的 schema
            /docs, /redoc  FastAPI 自动暴露的 UI
            /v1/*          已经是 v1 的不再镜像

        老 path 调用方仍然有效; 新调用方推荐用 /v1/<path>.
        废弃信号: 老 path 的响应 header 由 middleware 加 'Deprecation: ...'
        (本 Task 暂不做, future Phase 2 可加).
        """
        from fastapi.routing import APIRoute, APIWebSocketRoute

        EXCLUDE = {"/", "/ui", "/health", "/healthz", "/openapi.json", "/docs", "/redoc",
                   "/docs/oauth2-redirect"}

        # 必须复制一份 routes 列表, 避免迭代时修改原列表
        existing = list(self.app.routes)
        added = 0
        for route in existing:
            path = getattr(route, "path", None)
            if not path or path.startswith("/v1/") or path in EXCLUDE:
                continue
            v1_path = "/v1" + path
            try:
                if isinstance(route, APIRoute):
                    self.app.add_api_route(
                        path=v1_path,
                        endpoint=route.endpoint,
                        methods=list(route.methods or []),
                        name=f"{route.name}_v1" if route.name else None,
                        # 跳过 OpenAPI 文档展示, 避免一个 endpoint 出现两条 spec
                        include_in_schema=False,
                    )
                    added += 1
                elif isinstance(route, APIWebSocketRoute):
                    self.app.add_api_websocket_route(
                        path=v1_path,
                        endpoint=route.endpoint,
                        name=f"{route.name}_v1" if route.name else None,
                    )
                    added += 1
            except Exception as e:
                # 单条注册失败不阻止后面的, 只打 WARNING
                self.logger.warning(f"[VV] v1 alias 注册失败 {v1_path}: {e}")

        self.logger.info(f"[VV #82] 已注册 {added} 条 /v1/ 镜像路由, 老 path 仍然可用")

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
