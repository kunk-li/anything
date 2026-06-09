# -*- coding: utf-8 -*-
"""
build_business_layer (Task RR #78 拆出).

业务层装配: SimpleRAG + SimpleAgent + SimpleOrchestrator + 18 个工具注册.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from deps_module import BasicDeps, build_basic_deps
from document_store_module import LocalDocumentStore
from rag_module import SimpleRAG
from rag_module.extensions import (
    LLMQueryRewriter, LLMReranker, CrossEncoderReranker, BM25Retriever,
)
from agent_module import SimpleAgent
from orchestrator_module import SimpleOrchestrator

from llm_compat import call_llm_compat

from .tool_registry import DictToolRegistry
from .data_layer import build_data_layer


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

    # Task #49: BM25 单例 — 持久化到 run/bm25_index.json,
    # bootstrap 阶段尝试 load (没有索引文件就空跑, index_build 会逐步 add_chunks)
    bm25_index_path = deps.config.get_effective_value(
        "rag.bm25_index_path",
        env_var="ANYTHING_RAG_BM25_INDEX_PATH",
        default="bm25_index.json",
    )
    bm25_retriever = BM25Retriever()
    try:
        if bm25_retriever.load(bm25_index_path):
            deps.logger.info(f"BM25 索引已加载: path={bm25_index_path}, size={bm25_retriever.size}")
        else:
            deps.logger.info(f"BM25 索引不存在或为空, 等待 index_build 增量构建: path={bm25_index_path}")
    except Exception as e:
        deps.logger.warning(f"BM25 索引加载失败 (将以空索引继续): err={e}")

    rag = SimpleRAG(
        llm_client=llm_client,
        embedding=data_layer.get("embedding"),
        vector_db=data_layer.get("vector_db"),
        doc_store=data_layer.get("document_store"),
        reranker=reranker,
        query_rewriter=query_rewriter,
        state_store=data_layer.get("state_store"),  # Task #46 会话记忆
        bm25_retriever=bm25_retriever,              # Task #49 混合检索
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

    # 已有的两个核心工具 (描述硬编码兜底, 也写一份给 LLM 看)
    tool_registry.register(
        "rag_search", rag_search_tool,
        description='在知识库中检索相关文档片段。input: {"query": str, "top_k": int}',
    )
    tool_registry.register(
        "llm_generate", llm_generate_tool,
        description='调用大语言模型生成文本。input: {"prompt": str}',
    )

    # 扩展工具集 (Task #37 + #41): calculator / datetime / wikipedia / document_read
    # + regex_extract / text_stats / json_query / http_get / text_summarize
    from agent_module.tools import (
        calculator_tool, datetime_tool, wikipedia_tool,
        make_document_read_tool, TOOL_DESCRIPTIONS,
        regex_extract, text_stats, json_query, http_get,
        make_text_summarize_tool,
    )
    tool_registry.register("calculator", calculator_tool, description=TOOL_DESCRIPTIONS["calculator"])
    tool_registry.register("datetime", datetime_tool, description=TOOL_DESCRIPTIONS["datetime"])
    tool_registry.register("wikipedia", wikipedia_tool, description=TOOL_DESCRIPTIONS["wikipedia"])
    # document_read 闭包 doc_store_factory (按 tenant 动态构造)
    def _doc_store_for_tool(tenant_id: str):
        return LocalDocumentStore(deps=deps, tenant_id=tenant_id)
    tool_registry.register(
        "document_read",
        make_document_read_tool(_doc_store_for_tool),
        description=TOOL_DESCRIPTIONS["document_read"],
    )

    # Task #41: 5 个新增工具
    tool_registry.register("regex_extract", regex_extract, description=TOOL_DESCRIPTIONS["regex_extract"])
    tool_registry.register("text_stats", text_stats, description=TOOL_DESCRIPTIONS["text_stats"])
    tool_registry.register("json_query", json_query, description=TOOL_DESCRIPTIONS["json_query"])
    tool_registry.register("http_get", http_get, description=TOOL_DESCRIPTIONS["http_get"])
    # text_summarize 闭包 llm_client (复用 call_llm_compat)
    def _llm_call_for_summarize(prompt: str) -> str:
        return call_llm_compat(llm_client=data_layer.get("llm_client"), prompt=prompt)
    tool_registry.register(
        "text_summarize",
        make_text_summarize_tool(_llm_call_for_summarize),
        description=TOOL_DESCRIPTIONS["text_summarize"],
    )

    # Task #42: 再 6 个工具
    from agent_module.tools import (
        code_lint, make_email_send_tool, make_image_describe_tool,
        weather, currency_convert, python_sandbox,
    )
    tool_registry.register("code_lint", code_lint, description=TOOL_DESCRIPTIONS["code_lint"])
    tool_registry.register("weather", weather, description=TOOL_DESCRIPTIONS["weather"])
    tool_registry.register("currency_convert", currency_convert, description=TOOL_DESCRIPTIONS["currency_convert"])
    tool_registry.register("python_sandbox", python_sandbox, description=TOOL_DESCRIPTIONS["python_sandbox"])

    # Task HHH (#94): Web 通用搜索 (DuckDuckGo HTML, 免 API key)
    from agent_module.tools import web_search as _web_search
    tool_registry.register("web_search", _web_search, description=TOOL_DESCRIPTIONS["web_search"])

    # Task TTTT-6 (#143): 图片生成 (DashScope 万相 wanx-v1)
    from agent_module.tools import image_generate_tool as _image_generate
    from agent_module.tools.tools_impl.image_generate import TOOL_DESCRIPTION as _IG_DESC
    tool_registry.register("image_generate", _image_generate, description=_IG_DESC)

    # Task TTTT-4 (#141): PDF / Excel 读取 (pypdf + openpyxl)
    from agent_module.tools import pdf_read as _pdf_read, excel_read as _excel_read
    from agent_module.tools.tools_impl.pdf_excel_read import (
        PDF_READ_DESCRIPTION as _PDF_DESC,
        EXCEL_READ_DESCRIPTION as _XLS_DESC,
    )
    tool_registry.register("pdf_read", _pdf_read, description=_PDF_DESC)
    tool_registry.register("excel_read", _excel_read, description=_XLS_DESC)

    # Task TTTT-3 (#140): SQL 查询 (sqlite/postgres/mysql; 没配 conn 用 demo 内存表)
    from agent_module.tools import sql_query as _sql_query
    from agent_module.tools.tools_impl.sql_query import SQL_QUERY_DESCRIPTION as _SQL_DESC
    tool_registry.register("sql_query", _sql_query, description=_SQL_DESC)

    # Task TTTT-5 (#142): 浏览器自动化 (Playwright Chromium headless)
    from agent_module.tools import browser_visit as _browser_visit
    from agent_module.tools.tools_impl.browser_visit import BROWSER_VISIT_DESCRIPTION as _BV_DESC
    tool_registry.register("browser_visit", _browser_visit, description=_BV_DESC)

    # 计算机操作 (computer use): 截屏 + 鼠标/键盘控制桌面 (危险, 默认需审批; 需 pyautogui, 缺则降级)
    from agent_module.tools.tools_impl.computer_use import (
        make_computer_use_tool as _make_computer_use, COMPUTER_USE_DESCRIPTION as _CU_DESC,
    )
    tool_registry.register("computer_use", _make_computer_use(), description=_CU_DESC)

    # 系统状态只读查看 (system_info): CPU/内存/磁盘/网络/进程 — 让 agent 直接读真实指标再分析,
    # 而非教用户开任务管理器。只读无副作用 → 不进审批白名单 (不在 agent default_dangerous)。需 psutil, 缺则降级。
    from agent_module.tools.tools_impl.system_info import (
        make_system_info_tool as _make_system_info, SYSTEM_INFO_DESCRIPTION as _SI_DESC,
    )
    tool_registry.register("system_info", _make_system_info(), description=_SI_DESC)

    # 已安装软件版本/用法只读查 (software_info): 给软件名查版本+自带 --help, 或列已装清单 —
    # 让 agent 直接答"我装的 X 什么版本/怎么用"。只跑 PATH 已存在程序+固定参数, 只读 → 不进审批白名单。
    from agent_module.tools.tools_impl.software_info import (
        make_software_info_tool as _make_software_info, SOFTWARE_INFO_DESCRIPTION as _SW_DESC,
    )
    tool_registry.register("software_info", _make_software_info(), description=_SW_DESC)

    # 通用执行内核 (shell_exec): 真终端 — agent 自己写命令查/做任何事 (DB/系统/文件…),
    # 不再为每种需求造专用工具。逐条命令风险分级 (只读直执行; 破坏性/不透明需 approve_tools
    # 放行) + 审计。命令级判断在工具内, 故**不进** tool_approval_required (工具级审批会架空它)。
    from agent_module.tools.tools_impl.shell_exec import (
        make_shell_exec_tool as _make_shell_exec, SHELL_EXEC_DESCRIPTION as _SH_DESC,
    )
    tool_registry.register("shell_exec", _make_shell_exec(logger=deps.logger), description=_SH_DESC)

    # 技能加载 (use_skill, 集成 superpowers 机制): 按名把技能完整指南拉进上下文。只读 → 不进审批白名单。
    from agent_module.tools.tools_impl.use_skill import (
        make_use_skill_tool as _make_use_skill, USE_SKILL_DESCRIPTION as _US_DESC,
    )
    tool_registry.register("use_skill", _make_use_skill(), description=_US_DESC)

    # email_send: 从 yaml config 读 smtp 配置, 缺配置时工具自身降级 SERVICE_UNAVAILABLE
    smtp_cfg = deps.config.get_config("smtp", {}) or {}
    tool_registry.register(
        "email_send",
        make_email_send_tool(smtp_cfg),
        description=TOOL_DESCRIPTIONS["email_send"],
    )

    # image_describe: 闭包 llm_client (LLMService 才有 call_llm 方法, DummyLLMClient 没)
    llm_for_image = data_layer.get("llm_client")
    if hasattr(llm_for_image, "call_llm"):
        tool_registry.register(
            "image_describe",
            make_image_describe_tool(llm_for_image),
            description=TOOL_DESCRIPTIONS["image_describe"],
        )

    # Task KKK (#97) / FFF (#92): 长期记忆 — 默认走 InMemoryBackend (单进程),
    # 可通过 env ANYTHING_MEMORY_BACKEND=sqlite + ANYTHING_MEMORY_PATH=state/memory.db
    # 切到 SqliteBackend 让多 worker 共享 fact 库, 重启不丢.
    long_term_memory = None
    try:
        from long_term_memory_module import LongTermMemoryImpl
        from state_backend_module import InMemoryBackend, SqliteBackend
        import os as _os
        backend_kind = (_os.environ.get("ANYTHING_MEMORY_BACKEND") or "memory").lower()
        if backend_kind == "sqlite":
            mem_path = _os.environ.get("ANYTHING_MEMORY_PATH") or "state/memory.db"
            _os.makedirs(_os.path.dirname(mem_path) or ".", exist_ok=True)
            mem_backend = SqliteBackend(path=mem_path)
        else:
            mem_backend = InMemoryBackend()
        long_term_memory = LongTermMemoryImpl(
            backend=mem_backend,
            embedder=data_layer.get("embedding"),     # EEE: 语义查重 + cosine 搜索
            llm_client=llm_client,                    # EEE: extract_facts + summarize
        )
        deps.logger.info(
            f"[memory] long_term_memory 已启用 (backend={backend_kind}, "
            f"embedder={'on' if data_layer.get('embedding') else 'off'}, "
            f"llm={'on' if llm_client else 'off'})"
        )
    except Exception as _mem_err:
        deps.logger.warning(f"[memory] long_term_memory 启用失败 (忽略): {_mem_err}")

    # Phase4: 把 long_term_memory 也注入 RAG, 让"聊天"主路径同样懂使用者 (答前注入画像/相关
    # fact) + 越用越懂 (答后抽 fact)。rag 在 long_term_memory 之前构造, 故属性注入 + 补算
    # memory_enabled (__init__ 时 long_term_memory 还是 None, 算出来是 False)。
    if long_term_memory is not None and rag is not None:
        try:
            rag.long_term_memory = long_term_memory
            rag.memory_enabled = bool(deps.config.get_effective_value(
                "rag.memory_enabled", env_var="ANYTHING_RAG_MEMORY_ENABLED",
                default=True, value_type=bool))
            deps.logger.info(
                f"[memory] RAG 已接入 long_term_memory (memory_enabled={rag.memory_enabled})"
            )
        except Exception as _rm_err:
            deps.logger.warning(f"[memory] RAG 接入 long_term_memory 失败 (忽略): {_rm_err}")

    agent = SimpleAgent(
        state_store=data_layer.get("state_store"),
        tool_registry=tool_registry,
        # 墙钟超时 120s: 多轮 ReAct + 自我校验 + qwen 单次延迟, 简单任务 ~36s, 复杂任务给足
        # 余量"慢但能完成", 而不是 60s 撞 AGENT_TIMEOUT→504 干不成活 (用户优先完成而非快)。
        timeout=120,
        max_retries=2,
        session_prefix="session",
        deps=deps,
        long_term_memory=long_term_memory,   # Task FFF (#92): 注入到 Agent
        llm_client=llm_client,               # 真流式: Agent run_stream 直连 chat_stream
    )

    # 配置中枢 (执行计划②): 启动期校验 config.yaml 的 agent.* 未知 key → WARN (防拼写错/废弃漂移)。
    # fail-safe: 校验本身异常不阻断启动。
    try:
        from agent_module.config.schema import validate_agent_config
        validate_agent_config(deps.config, deps.logger)
    except Exception as _cfg_err:
        deps.logger.warning(f"[config] agent 配置校验跳过 (异常): {_cfg_err}")

    # 外部工具连接 (RFC Stage1): 从 config agent.external_tools 注册 HTTP 外部工具,
    # 默认并入 agent 审批白名单 (human-in-loop)。无配置/失败均不阻断启动 (fail-safe)。
    _ext_providers: list = []   # 生命周期管理: 留引用, 经 result 透出供退出时 close (MCP 子进程)
    try:
        from agent_module.tools.external import (
            build_providers_from_config, register_external_tools,
        )
        _ext_providers = build_providers_from_config(deps.config)
        if _ext_providers:
            _ext_approval = register_external_tools(tool_registry, _ext_providers)
            agent.tool_approval_required.update(_ext_approval)
            deps.logger.info(
                f"[ext-tools] 注册外部工具 {len(_ext_approval)} 个 (默认需审批): "
                f"{sorted(_ext_approval)}"
            )
    except Exception as _ext_err:
        deps.logger.warning(f"[ext-tools] 外部工具注册失败 (忽略): {_ext_err}")

    # Task EE (#65): spawn_subagent — 需要拿到 parent agent 引用, 所以在 new
    # SimpleAgent 之后再注册到同一个 tool_registry. 子 agent 跑 ReAct 时复用
    # 这个 registry 但用 _RestrictedRegistry 限制可见工具子集.
    try:
        from agent_module.tools import make_spawn_subagent_tool
        tool_registry.register(
            "spawn_subagent",
            make_spawn_subagent_tool(agent),
            description=TOOL_DESCRIPTIONS["spawn_subagent"],
        )
    except Exception as e:
        deps.logger.warning(f"[bootstrap] spawn_subagent 注册失败 (忽略): {e}")

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
        # Task #49: 暴露给 index_build 阶段往里 add_chunks + save
        "bm25_retriever": bm25_retriever,
        "bm25_index_path": bm25_index_path,
        # Task KKK (#97): 透传给 application_layer 让 ApiService /memory/* 路由可用
        "long_term_memory": long_term_memory,
        # 生命周期管理: 外部工具 providers (含 MCP 连接), 退出时可逐个 .close() 释放子进程/会话
        "external_tool_providers": _ext_providers,
    }
