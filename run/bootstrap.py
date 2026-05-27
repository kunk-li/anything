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
from deps_module import BasicDeps, build_basic_deps, StartupError, is_dev_mode

# 数据层
from document_parser_module.core.impl import LocalDocumentParser
from state_store_module import LocalStateStore
from llm_adapter_module import LLMService
from vector_db_module import FaissVectorDB
from document_store_module import LocalDocumentStore
from embedding_module import STEmbedding

# 业务层
from rag_module import SimpleRAG
from rag_module.extensions import LLMQueryRewriter, LLMReranker, CrossEncoderReranker
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


def _build_component(name: str, factory, hint: str = "") -> Any:
    """构建单个组件,失败时根据 dev_mode 决定是抛 StartupError 还是返回 None。

    生产/严格模式(默认): 抛 StartupError,系统拒绝启动
    开发模式 (ANYTHING_DEV_MODE=1): 打印 WARNING 并返回 None,允许下游用占位
    """
    try:
        return factory()
    except Exception as e:
        if is_dev_mode():
            print(f"[bootstrap][DEV_MODE] {name} 初始化失败,回退 None: {e}")
            return None
        raise StartupError(
            component=name,
            reason=str(e),
            hint=hint or "设置环境变量 ANYTHING_DEV_MODE=1 可在本地用占位实现继续运行",
        ) from e


def build_data_layer(
    use_dummy_llm: Optional[bool] = None,
    deps: Optional[BasicDeps] = None,
) -> Dict[str, Any]:
    """构建数据层(fail-fast + 共享 BasicDeps)。

    参数:
        use_dummy_llm: None 表示根据 dev_mode 自动决定(dev=True / 生产=False);
                       显式 True/False 优先于环境变量
        deps: 基础依赖容器;未提供时构造一份并向数据层各 impl 注入,确保整条
              装配链共享同一个 ConfigManager/SystemLogger 等

    生产/严格模式(默认):
        - 任一关键组件初始化失败 → 抛 StartupError,系统拒绝启动
        - 不再静默用 DummyLLMClient / None 兜底

    开发模式(ANYTHING_DEV_MODE=1):
        - 关键组件失败时打印 WARNING 并回退 None
        - LLM 失败时使用 DummyLLMClient(便于无网络/无 API key 时本地调试)
    """
    dev = is_dev_mode()
    if use_dummy_llm is None:
        use_dummy_llm = dev
    deps = deps or build_basic_deps()

    # LLM client: 失败时 dev 模式回退 Dummy,生产抛 StartupError
    try:
        llm_client = LLMService(deps=deps)
        print(f"[bootstrap] llm_client 已启用真实 LLMService: {type(llm_client).__name__}")
    except Exception as e:
        if use_dummy_llm:
            print(f"[bootstrap][DEV_MODE] llm_client 初始化失败,回退 DummyLLMClient: {e}")
            llm_client = DummyLLMClient()
        else:
            raise StartupError(
                component="llm_client",
                reason=str(e),
                hint="检查 llm 配置(api_key/api_base/model_name);或设置 ANYTHING_DEV_MODE=1 启用 DummyLLMClient",
            ) from e

    embedding = _build_component("embedding", lambda: STEmbedding(deps=deps),
                                 hint="检查 sentence-transformers 模型是否可用")
    vector_db = _build_component("vector_db", lambda: FaissVectorDB(deps=deps),
                                 hint="检查 faiss-cpu 是否安装,以及 vector_db.vector_dimension 配置")
    document_store = _build_component("document_store", lambda: LocalDocumentStore(deps=deps),
                                      hint="检查 document_store.dir 配置与目录写入权限")
    state_store = _build_component("state_store", lambda: LocalStateStore(deps=deps),
                                   hint="检查 state_store.dir 配置与目录写入权限")
    document_parser = _build_component("document_parser", lambda: LocalDocumentParser(deps=deps),
                                       hint="检查文档解析依赖(pypdf/python-docx 等)")

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
    deps = deps or build_basic_deps()
    # 把 deps 也传到 data_layer,确保全链路共享同一份基础组件
    data_layer = data_layer or build_data_layer(deps=deps)

    # 构造一个共享的 llm_call 闭包,供 rewriter / reranker 使用
    llm_client = data_layer.get("llm_client")

    def _llm_call(prompt: str) -> str:
        return call_llm_compat(llm_client=llm_client, prompt=prompt)

    query_rewriter = LLMQueryRewriter(llm_call=_llm_call) if llm_client is not None else None

    # Reranker 类型可通过 config 切换:
    #   rag.reranker_type = "cross_encoder" (默认,本地推理,快且稳定)
    #                       "llm"            (LLM 调用 rerank, 慢且需 API key)
    reranker_type = deps.config.get_effective_value(
        "rag.reranker_type",
        env_var="ANYTHING_RAG_RERANKER_TYPE",
        default="cross_encoder",
    )
    if reranker_type == "cross_encoder":
        reranker = CrossEncoderReranker()
    elif reranker_type == "llm" and llm_client is not None:
        reranker = LLMReranker(llm_call=_llm_call)
    else:
        reranker = None

    rag = SimpleRAG(
        llm_client=llm_client,
        embedding=data_layer.get("embedding"),
        vector_db=data_layer.get("vector_db"),
        doc_store=data_layer.get("document_store"),
        reranker=reranker,
        query_rewriter=query_rewriter,
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
    data = build_data_layer(deps=deps)
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
