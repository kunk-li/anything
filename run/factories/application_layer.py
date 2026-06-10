# -*- coding: utf-8 -*-
"""
build_application_layer (Task RR #78 拆出).

应用层装配: ApiService (FastAPI) + ConsoleApp. 按需构建避免 API 启动时
强依赖 ConsoleApp.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from deps_module import BasicDeps, build_basic_deps
from document_store_module import LocalDocumentStore
from api_service_module import ApiService
from api_service_module.utils.scheduler import TaskScheduler, build_maintenance_task
from console_app_module.core.impl import ConsoleApp

from .interface_layer import build_interface_layer


def _make_graceful_shutdown(scheduler: Any, providers: Any):
    """优化②: 返回一个收尾函数 — 停调度线程 + 关外部工具连接 (MCP 子进程)。
    各自 fail-safe (单个失败不影响其余, 不抛)。提成纯函数便于单测 (无需起 FastAPI 生命周期)。"""
    def _shutdown() -> None:
        if scheduler is not None:
            try:
                scheduler.stop()
            except Exception:
                pass
        for p in (providers or []):
            try:
                p.close()
            except Exception:
                pass
    return _shutdown


def _build_scheduler(handler: Any, deps: BasicDeps) -> Optional[TaskScheduler]:
    """方向1/4: 按配置构建并启动 TaskScheduler。默认关 — 无配置任务则返 None (零后台线程)。

    任务来源 (合并):
      - scheduler.tasks: 通用定时任务列表 (每条 {id, schedule, type, body, enabled})。
      - agent.maintenance_schedule: 便捷自维护扫描 (方向1/4)。**仅当 enable_self_reflection=true**
        才注册 (否则 maintenance_scan 钩子不触发, body 会被当普通 agent 任务跑 → 防呆 gate)。
    无任务 → None (不起线程 / 零意外 LLM 成本)。单条声明出错只 WARN, 不阻断启动 (fail-safe)。
    """
    cfg = deps.config
    log = deps.logger
    tasks: list = []

    sched_cfg = cfg.get_config("scheduler", {}) or {}
    for t in (sched_cfg.get("tasks") or []):
        if isinstance(t, dict):
            tasks.append(t)

    maint_schedule = str(cfg.get_config("agent.maintenance_schedule", "") or "").strip()
    if maint_schedule:
        self_reflect_on = cfg.get_effective_value(
            "agent.enable_self_reflection", env_var="ANYTHING_AGENT_SELF_REFLECTION",
            default=False, value_type=bool,
        )
        if self_reflect_on:
            tasks.append(build_maintenance_task(
                schedule=maint_schedule,
                scope=cfg.get_config("agent.maintenance_scope", None),
                auto_apply=cfg.get_config("agent.maintenance_auto_apply", None),
            ))
        elif log:
            log.warning(
                "[scheduler] agent.maintenance_schedule 已配置但 enable_self_reflection=false, "
                "跳过自维护扫描注册 (先开 agent.enable_self_reflection)"
            )

    if not tasks:
        return None

    scheduler = TaskScheduler(handler=handler, logger=log, audit_logger=deps.audit_logger)
    for t in tasks:
        try:
            scheduler.register(t)
        except Exception as e:
            if log:
                log.warning(f"[scheduler] 任务注册失败 (跳过): id={t.get('id')!r} err={e}")
    scheduler.start()
    if log:
        log.info(f"[scheduler] 已启动, 注册 {len(scheduler.list_tasks())} 个定时任务")
    return scheduler


def build_application_layer(
    interface_layer: Optional[Dict[str, Any]] = None,
    build_api: bool = True,
    build_console: bool = False,
    deps: Optional[BasicDeps] = None,
    data_layer: Optional[Dict[str, Any]] = None,
    business_layer: Optional[Dict[str, Any]] = None,  # Task #49: 复用 bm25_retriever
) -> Dict[str, Any]:
    """构建应用层（共享 BasicDeps,按需构建,避免 API 启动时强依赖 ConsoleApp）

    data_layer: 可选, 透传给 ApiService 让 /config/models 端点能拿到 LLMService。
    business_layer: 可选, 透传给 _index_runner 让上传文件自动喂 BM25 索引 (Task #49)。
                未传时自动 build_data_layer 一次 (跟其他层一样的 DI 共享原则)。
    """
    deps = deps or build_basic_deps()
    interface_layer = interface_layer or build_interface_layer(deps=deps)
    handler = interface_layer["handler"]

    result: Dict[str, Any] = {}

    if build_api:
        # 给 GET /documents/{doc_id}/preview 注入按租户构造 doc_store 的工厂。
        # 每次 preview 请求按请求的 tenant_id 现 new (轻量, hash_map 加载 ~ms),
        # 失败时 ApiService 返回 PREVIEW_NOT_SUPPORTED, 不影响其他端点。
        def _doc_store_factory(tenant_id: str):
            return LocalDocumentStore(deps=deps, tenant_id=tenant_id)

        # llm_service 取自 data_layer; 没传 data_layer 就尝试现构造 (回退一份 DummyLLMClient
        # 时 list_models 会返回空列表, /config/models 端点也降级到 SERVICE_UNAVAILABLE)。
        llm_service = None
        if data_layer is not None:
            candidate = data_layer.get("llm_client")
            # 只把 LLMService 真实实例传过去, DummyLLMClient 没有 list_models 接口
            if hasattr(candidate, "list_models") and hasattr(candidate, "register_or_update_model"):
                llm_service = candidate

        # index_runner: 上传文件后自动触发 parse + chunk + embed + upsert,
        # 让 /documents/upload 真的能让 RAG 立刻查到。复用现有 data_layer 不重 new。
        # 走 default tenant 的 vector_store (多租户运行期 upload 走 PR4+ 路径)。
        # Task #49: 同时把 chunks 写入 BM25 索引, 让混合检索拿到新文档。
        bm25_for_index = business_layer.get("bm25_retriever") if business_layer else None
        bm25_index_path_for_index = business_layer.get("bm25_index_path") if business_layer else None
        index_runner = None
        rebuild_runner = None
        if data_layer is not None and data_layer.get("embedding") and data_layer.get("vector_db"):
            def _index_runner(file_path: str):
                from index_build import build_index as _build_index
                return _build_index(
                    source_type="file",
                    source_path=file_path,
                    data_layer=data_layer,
                    bm25_retriever=bm25_for_index,
                    bm25_index_path=bm25_index_path_for_index,
                )
            index_runner = _index_runner

            # P14: 全量重建 (POST /index/build 后台线程跑) — 清向量库+BM25 后
            # 以 document_store 已存文档为源重灌, 清理删除残留/参数变更重建
            def _rebuild_runner(progress_cb=None):
                from index_build import rebuild_index as _rebuild_index
                return _rebuild_index(
                    data_layer=data_layer,
                    bm25_retriever=bm25_for_index,
                    bm25_index_path=bm25_index_path_for_index,
                    progress_cb=progress_cb,
                )
            rebuild_runner = _rebuild_runner

        # Task S: 透传 rag + vector_db 让 /admin/status 能拿到运行期状态
        rag_for_admin = business_layer.get("rag") if business_layer else None
        vec_db_for_admin = data_layer.get("vector_db") if data_layer else None
        # Task KKK (#97): 透传长期记忆给 /memory/* 5 路由
        long_term_memory = business_layer.get("long_term_memory") if business_layer else None
        # Task SSS (#105): 透传 state_store 给 /sessions/* 3 路由
        state_store = data_layer.get("state_store") if data_layer else None
        # Task FFFF (#123): 透传 tool_registry 给 /agent/tools 路由
        tool_registry = business_layer.get("tool_registry") if business_layer else None
        # 执行计划⑥/⑦: 透传 agent 给 /agent/maintenance/* + /config/agent/* 路由
        agent_for_routes = business_layer.get("agent") if business_layer else None

        # 方向1/4: 接线 TaskScheduler (默认关 — 无配置定时任务时返 None, 不起线程)。
        # 让 /scheduler/* 路由真正可用 + 自维护扫描 (reconcile/consolidate/prune / 全域) 能定时触发。
        scheduler = _build_scheduler(handler, deps)

        result["api_service"] = ApiService(
            handler=handler,
            deps=deps,
            document_store_factory=_doc_store_factory,
            llm_service=llm_service,
            index_runner=index_runner,
            rag_runner=rag_for_admin,
            vector_db=vec_db_for_admin,
            long_term_memory=long_term_memory,
            state_store=state_store,
            tool_registry=tool_registry,
            scheduler=scheduler,
            agent=agent_for_routes,
            bm25_retriever=bm25_for_index,
            bm25_index_path=bm25_index_path_for_index,
            rebuild_runner=rebuild_runner,
        )

        # 优化②: app 退出统一收尾 — 停调度线程 + 关外部工具连接 (MCP 子进程)。daemon 线程/子进程
        # 本随进程退出, 这里显式收口让 graceful shutdown / 重启 / 嵌入场景更干净。无可收时不挂钩子。
        _ext_providers = (business_layer or {}).get("external_tool_providers") or []
        _fastapi_app = getattr(result["api_service"], "app", None)
        if _fastapi_app is not None and (scheduler is not None or _ext_providers):
            # FastAPI 0.136/Starlette 1.0 移除了 app.add_event_handler — 这里原先用它,
            # 配了 scheduler/外部工具的部署在新版下启动即 AttributeError; 改走 router
            _fastapi_app.router.add_event_handler(
                "shutdown", _make_graceful_shutdown(scheduler, _ext_providers))

    if build_console:
        console_app = ConsoleApp(
            handler=handler,
            input_provider=None,
            renderer=None,
            history_store=None,
            deps=deps,
        )
        result["console_app"] = console_app

    return result
