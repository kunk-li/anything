# -*- coding: utf-8 -*-
"""
统一 bootstrap 组装入口
负责把基础层、数据层、业务层、接口层、应用层串接起来，形成最小可运行系统。
"""

from typing import Any, Dict, Optional

# 基础层
from common_utils_module import CommonUtils
from config_module import ConfigManager
from log_module import SystemLogger
from exception_module import ExceptionHandler
from deps_module import BasicDeps, build_basic_deps

# 数据层
from document_parser_module.core.impl import LocalDocumentParser
from state_store_module import LocalStateStore
from llm_adapter_module import LLMService
from vector_db_module import FaissVectorDB
from document_store_module import LocalDocumentStore
from embedding_module import STEmbedding

# 业务层
from rag_module import SimpleRAG
from agent_module import SimpleAgent
from orchestrator_module import SimpleOrchestrator

# 接口层
from request_response_module import RequestHandler

# 应用层
from api_service_module import ApiService
from console_app_module.core.impl import ConsoleApp

from llm_compat import DummyLLMClient, call_llm_compat


class DictToolRegistry:
    """最小工具注册表实现，兼容 register/get 风格"""

    def __init__(self):
        self._tools: Dict[str, Any] = {}

    def register(self, name: str, tool: Any) -> None:
        self._tools[name] = tool

    def get(self, name: str) -> Any:
        return self._tools.get(name)

    def list_tools(self):
        return list(self._tools.keys())



def build_basic_support() -> Dict[str, Any]:
    """构建基础支撑层（基于 BasicDeps 容器,内部单例共享）。

    返回 dict 以保持向后兼容;若调用方需要容器引用,推荐改用 build_basic_deps()。
    """
    deps = build_basic_deps()
    return {
        "config": deps.config,
        "logger": deps.logger,
        "exception_handler": deps.exception_handler,
        "utils": deps.utils,
        "deps": deps,  # 暴露容器本身,便于业务/接口/应用层注入
    }


def build_data_layer(use_dummy_llm: bool = True) -> Dict[str, Any]:
    """
    构建数据层。
    说明：
    - 这里优先采用“能跑起来”的最小装配。
    - 若真实 provider 未接通，可先使用占位 LLM。
    """
    try:
        llm_client = LLMService()
        print(f"[bootstrap] llm_client 已启用真实 LLMService: {type(llm_client).__name__}")
        print(f"[bootstrap] llm_client methods: {[x for x in dir(llm_client) if not x.startswith('_')][:80]}")
    except Exception as e:
        print(f"[bootstrap] llm_client 初始化失败，回退 DummyLLMClient: {e}")
        llm_client = DummyLLMClient() if use_dummy_llm else None

    try:
        embedding = STEmbedding()
    except Exception as e:
        print(f"[bootstrap] embedding 初始化失败: {e}")
        embedding = None

    try:
        vector_db = FaissVectorDB()
    except Exception as e:
        print(f"[bootstrap] vector_db 初始化失败: {e}")
        vector_db = None

    try:
        document_store = LocalDocumentStore()
    except Exception as e:
        print(f"[bootstrap] document_store 初始化失败: {e}")
        document_store = None

    try:
        state_store = LocalStateStore()
    except Exception as e:
        print(f"[bootstrap] state_store 初始化失败: {e}")
        state_store = None

    try:
        document_parser = LocalDocumentParser()
    except Exception as e:
        print(f"[bootstrap] document_parser 初始化失败: {e}")
        document_parser = None

    return {
        "llm_client": llm_client,
        "embedding": embedding,
        "vector_db": vector_db,
        "document_store": document_store,
        "state_store": state_store,
        "document_parser": document_parser,
    }


def build_business_layer(
    data_layer: Optional[Dict[str, Any]] = None,
    deps: Optional[BasicDeps] = None,
) -> Dict[str, Any]:
    """构建业务层（共享 BasicDeps，避免每个模块重复 new 基础组件）"""
    data_layer = data_layer or build_data_layer()
    deps = deps or build_basic_deps()

    rag = SimpleRAG(
        llm_client=data_layer.get("llm_client"),
        embedding=data_layer.get("embedding"),
        vector_db=data_layer.get("vector_db"),
        doc_store=data_layer.get("document_store"),
        reranker=None,
        deps=deps,
    )

    tool_registry = DictToolRegistry()

    # rag_search 工具：统一走 rag.run(request_dict)
    def rag_search_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
        return rag.run({
            "query": payload.get("query", ""),
            "top_k": payload.get("top_k", 5),
            "trace_id": payload.get("trace_id"),
            "session_id": payload.get("session_id"),
            "extra_params": payload.get("extra_params", {}),
        })

    # llm_generate 工具：兼容 DummyLLMClient 与更正式客户端
    def llm_generate_tool(payload: Dict[str, Any]) -> Dict[str, Any]:
        prompt = payload.get("prompt", "")
        llm_client = data_layer.get("llm_client")

        text = call_llm_compat(
            llm_client=llm_client,
            prompt=prompt,
            trace_id=payload.get("trace_id"),
        )

        return {
            "code": "SUCCESS",
            "message": "ok",
            "data": {"text": text},
            "trace_id": payload.get("trace_id"),
            "retryable": False,
            "details": None,
        }

    tool_registry.register("rag_search", rag_search_tool)
    tool_registry.register("llm_generate", llm_generate_tool)

    agent = SimpleAgent(
        state_store=data_layer.get("state_store"),
        tool_registry=tool_registry,
        timeout=60,
        max_retries=2,
        session_prefix="session",
        deps=deps,
    )

    orchestrator = SimpleOrchestrator(
        rag_runner=rag,
        agent_runner=agent,
        deps=deps,
    )

    return {
        "rag": rag,
        "agent": agent,
        "orchestrator": orchestrator,
        "tool_registry": tool_registry,
    }


def build_interface_layer(
    business_layer: Optional[Dict[str, Any]] = None,
    deps: Optional[BasicDeps] = None,
) -> Dict[str, Any]:
    """构建接口层（共享 BasicDeps）"""
    deps = deps or build_basic_deps()
    business_layer = business_layer or build_business_layer(deps=deps)
    handler = RequestHandler(orchestrator=business_layer["orchestrator"], deps=deps)
    return {"handler": handler}


def build_application_layer(
    interface_layer: Optional[Dict[str, Any]] = None,
    build_api: bool = True,
    build_console: bool = False,
    deps: Optional[BasicDeps] = None,
) -> Dict[str, Any]:
    """构建应用层（共享 BasicDeps,按需构建,避免 API 启动时强依赖 ConsoleApp）"""
    deps = deps or build_basic_deps()
    interface_layer = interface_layer or build_interface_layer(deps=deps)
    handler = interface_layer["handler"]

    result: Dict[str, Any] = {}

    if build_api:
        result["api_service"] = ApiService(handler=handler, deps=deps)

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


def build_handler() -> RequestHandler:
    """直接构建统一 RequestHandler（DI 共享一份 BasicDeps）"""
    deps = build_basic_deps()
    business_layer = build_business_layer(deps=deps)
    interface_layer = build_interface_layer(business_layer=business_layer, deps=deps)
    return interface_layer["handler"]

def build_api_app():
    """构建 FastAPI app（DI 共享一份 BasicDeps）"""
    deps = build_basic_deps()
    app_layer = build_application_layer(build_api=True, build_console=False, deps=deps)
    return app_layer["api_service"].app



def build_console_app() -> ConsoleApp:
    """构建控制台应用（DI 共享一份 BasicDeps）"""
    deps = build_basic_deps()
    app_layer = build_application_layer(build_api=False, build_console=True, deps=deps)
    return app_layer["console_app"]


def build_all(include_console: bool = False) -> Dict[str, Any]:
    """完整构建所有层（基础依赖 DI 共享,便于调试或测试）"""
    basic = build_basic_support()
    deps = basic["deps"]  # 由 build_basic_support 暴露的容器
    data = build_data_layer()
    business = build_business_layer(data_layer=data, deps=deps)
    interface = build_interface_layer(business_layer=business, deps=deps)
    app = build_application_layer(
        interface_layer=interface,
        build_api=True,
        build_console=include_console,
        deps=deps,
    )

    return {
        "basic_support": basic,
        "data_layer": data,
        "business_layer": business,
        "interface_layer": interface,
        "application_layer": app,
    }
