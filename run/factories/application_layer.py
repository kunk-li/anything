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
from console_app_module.core.impl import ConsoleApp

from .interface_layer import build_interface_layer


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

        # Task S: 透传 rag + vector_db 让 /admin/status 能拿到运行期状态
        rag_for_admin = business_layer.get("rag") if business_layer else None
        vec_db_for_admin = data_layer.get("vector_db") if data_layer else None
        # Task KKK (#97): 透传长期记忆给 /memory/* 5 路由
        long_term_memory = business_layer.get("long_term_memory") if business_layer else None

        result["api_service"] = ApiService(
            handler=handler,
            deps=deps,
            document_store_factory=_doc_store_factory,
            llm_service=llm_service,
            index_runner=index_runner,
            rag_runner=rag_for_admin,
            vector_db=vec_db_for_admin,
            long_term_memory=long_term_memory,
        )

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
