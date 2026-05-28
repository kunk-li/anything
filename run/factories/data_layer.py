# -*- coding: utf-8 -*-
"""
build_data_layer (Task RR #78 拆出).

数据层装配: LLMService + embedding + vector_db + document_store +
            state_store + document_parser. 共享 BasicDeps.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from deps_module import BasicDeps, build_basic_deps, StartupError, is_dev_mode
from document_parser_module.core.impl import LocalDocumentParser
from state_store_module import LocalStateStore
from llm_adapter_module import LLMService
from vector_db_module import FaissVectorDB
from document_store_module import LocalDocumentStore
from embedding_module import STEmbedding

from llm_compat import DummyLLMClient

from .basic_support import _build_component


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
