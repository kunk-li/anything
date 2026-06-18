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
    FeedbackRoutesMixin,  # Task PM-2
    KbRoutesMixin,  # Task PM-5
    ProjectsRoutesMixin,  # Part B: 多项目
    FrontendRoutesMixin,
)
from .support import SecurityMixin, MetricsMixin, UploadJanitorMixin


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
    FeedbackRoutesMixin,  # Task PM-2
    KbRoutesMixin,  # Task PM-5
    ProjectsRoutesMixin,  # Part B: 多项目
    FrontendRoutesMixin,
    SecurityMixin,
    MetricsMixin,
    UploadJanitorMixin,
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
        agent=None,                # 执行计划⑥/⑦: /agent/maintenance/* + /config/agent/* 路由用
        bm25_retriever=None,       # P4: DELETE /documents 在线摘除 BM25 条目
        bm25_index_path=None,      # P4: 摘除后持久化路径
        rebuild_runner=None,       # P14: POST /index/build 全量重建入口 (factory 闭包)
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
        # 执行计划⑥/⑦: 注入 agent 实例供 /agent/maintenance/* (维护提议/审批) 与
        # /config/agent/* (配置 dump/set) 路由用. None 时这些路由返 SERVICE_UNAVAILABLE.
        self.agent = agent
        # P4/P14: 删除链路在线摘 BM25 + 索引全量重建
        self.bm25_retriever = bm25_retriever
        self.bm25_index_path = bm25_index_path
        self.rebuild_runner = rebuild_runner
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
        # JWT (auth_type=jwt 用): secret 未配置/未解析占位符 -> 视为未配置,
        # _check_auth 走 fail-closed (全部 401), dev 模式下面会自动关 auth。
        _jwt_secret = str(self.config.get_config("security.jwt_secret", "") or "")
        self.jwt_secret = "" if _jwt_secret.startswith("${") else _jwt_secret
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
        # jwt 同理: dev 模式 secret 未配置自动关; 生产不关 (fail-closed, 全部 401) 但留 WARN
        if self.auth_enabled and self.auth_type == "jwt" and not self.jwt_secret:
            if dev_mode:
                self.logger.warning(
                    "[security] DEV_MODE 检测到 jwt_secret 未配置, 自动关闭 auth。"
                    "生产部署请 export JWT_SECRET, 不要设 ANYTHING_DEV_MODE。"
                )
                self.auth_enabled = False
            else:
                self.logger.warning(
                    "[security] auth_type=jwt 但 jwt_secret 未配置: "
                    "所有请求将被 401 (fail-closed)。请 export JWT_SECRET。"
                )
        # 内部白名单 (本期占位, 真实使用在 §4.3 冲突处理): 配置 internal_whitelist 后, 这些 IP 可仅靠 body tenant_id
        self.internal_whitelist: List[str] = list(
            self.config.get_config("security.internal_whitelist", []) or []
        )
        # admin keys: 配置后 /config/models 等管理写端点仅持这些 key 可调 (403 拦截);
        # 不配置则维持旧约定 — 生产在网关层屏蔽 /config/*。
        self.admin_api_keys: set = {
            str(k) for k in (self.config.get_config("security.admin_api_keys", []) or []) if k
        }
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

        # P1: uploads 保留期清理 — uvicorn startup 才起线程 (普通 TestClient 不触发,
        # 单测不会带起后台清理)。retention<=0 或没注入 doc store 时不启动。
        # 注: FastAPI 0.136/Starlette 1.0 移除了 app.add_event_handler, 走 router
        self.app.router.add_event_handler("startup", self._start_upload_janitor)

        self.logger.info("API 服务模块初始化完成")

    # 指标/配额 (_is_known_tenant/_check_qps_quota/_bucket_tenant/_record_metrics/
    #   _render_prometheus_metrics) -> support/metrics.py (MetricsMixin)
    def _register_middlewares(self) -> None:
        @self.app.middleware("http")
        async def trace_middleware(request: Request, call_next):
            trace_id = request.headers.get("X-Request-Id") or self._generate_trace_id()
            request.state.trace_id = trace_id
            request.state.start_time = time.time()

            # 安全 (生产冒烟发现真 bug): 在此 enforce 鉴权. 之前 _check_auth 定义了却
            # 从没在 HTTP middleware 调用 → 即使 security.auth_enabled=true, 所有 REST
            # 端点 (/invoke /documents /sessions /config /admin ...) 也全部裸奔.
            # public 路径 (前端静态 / 健康检查 / API 文档) 豁免, 否则浏览器打不开 UI.
            if not self._is_public_path(request.url.path):
                _auth_fail = self._check_auth(request, trace_id)
                if _auth_fail is not None:
                    _auth_fail.headers["X-Request-Id"] = trace_id
                    return _auth_fail

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
        self._register_feedback_routes()  # Task PM-2: /feedback 3 路由
        self._register_kb_routes()  # Task PM-5: /kb 6 路由
        self._register_projects_routes()  # Part B: /projects 3 路由 (多项目注册表)
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
    # 鉴权/租户解析 (_reconcile_tenant_id/_build_key_to_tenant_index/_resolve_tenant_from_auth/
    #   _extract_bearer_token/_decode_jwt/_is_internal_ip/_is_internal_host/_check_auth/
    #   _check_admin) -> support/security.py (SecurityMixin)
    # 上传清理 (_start_upload_janitor/_clean_uploads_once) -> support/upload_janitor.py
    @staticmethod
    def _is_public_path(path: str) -> bool:
        """鉴权豁免的 public 路径: 前端静态资源 / 健康检查 / API 文档.
        其余 (业务 API) 在 auth_enabled 时都需带 X-API-Key. dev 模式 auth 本就关, 不受影响.
        """
        p = path or "/"
        if p in ("/", "/health", "/favicon.ico", "/openapi.json", "/docs", "/redoc"):
            return True
        return p.startswith(("/static/", "/docs/", "/redoc/"))

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


