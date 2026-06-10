# -*- coding: utf-8 -*-
"""
RAG 模块具体实现类
负责 query 规范化、检索、上下文拼装、生成回答与统一响应封装
"""

import time
from typing import Dict, Any, List, Optional

from llm_compat import call_llm_compat
from .base import BaseRAG

from deps_module import BasicDeps, build_basic_deps, handle_exception_to_envelope
from observability_module import trace_span
from common_utils_module import get_project_memory


class SimpleRAG(BaseRAG):
    """标准 RAG 实现：retrieve -> generate -> citations -> unified response"""

    def __init__(
        self,
        llm_client=None,
        embedding=None,
        vector_db=None,
        doc_store=None,
        reranker=None,
        query_rewriter=None,
        state_store=None,  # Task #46: 会话记忆 (从这里读历史 + 写新轮)
        bm25_retriever=None,  # Task #49: 混合检索, 关键字稀疏召回
        long_term_memory=None,  # Phase4: 用户模型/跨会话学习 (个性化 + 越用越懂)
        deps: Optional[BasicDeps] = None,
    ):
        # 基础依赖优先走 DI 注入；未注入时构造一套（向后兼容）
        deps = deps or build_basic_deps()
        self.utils = deps.utils
        self.logger = deps.logger
        self.config = deps.config
        self.exception_handler = deps.exception_handler

        self.llm_client = llm_client
        self.embedding = embedding
        self.vector_db = vector_db
        self.doc_store = doc_store
        self.reranker = reranker
        self.query_rewriter = query_rewriter
        self.state_store = state_store
        self.bm25_retriever = bm25_retriever
        self.long_term_memory = long_term_memory

        # P8 取文缓存: {doc_id: (content, expires_at)} — meta 不再存全文后,
        # 命中 chunk 按偏移从 doc_store 抠原文; 一次查询多个 chunk 常落同一 doc。
        self._doc_content_cache: Dict[str, Any] = {}
        # P15 KB doc_ids 短缓存: {kb_id: (set, expires_at)} — 原先每次查询现开 sqlite
        self._kb_cache: Dict[str, Any] = {}

        # 关键配置项走 get_effective_value, 允许环境变量覆盖
        # (运维不改代码即可调参; 详见 docs/configuration-priority.md)
        self.top_k_retrieve = self.config.get_effective_value(
            "rag.top_k_retrieve", env_var="ANYTHING_RAG_TOP_K_RETRIEVE",
            default=50, value_type=int,
        )
        self.top_k_rerank = self.config.get_effective_value(
            "rag.top_k_rerank", env_var="ANYTHING_RAG_TOP_K_RERANK",
            default=8, value_type=int,
        )
        self.max_context_tokens = self.config.get_effective_value(
            "rag.max_context_tokens", env_var="ANYTHING_RAG_MAX_CONTEXT_TOKENS",
            default=3000, value_type=int,
        )
        self.max_chunk_in_prompt_tokens = self.config.get_effective_value(
            "rag.max_chunk_in_prompt_tokens", env_var="ANYTHING_RAG_MAX_CHUNK_TOKENS",
            default=600, value_type=int,
        )
        self.enable_rerank = self.config.get_effective_value(
            "rag.enable_rerank", env_var="ANYTHING_RAG_ENABLE_RERANK",
            default=False, value_type=bool,
        )
        self.enable_rewrite = self.config.get_effective_value(
            "rag.enable_rewrite", env_var="ANYTHING_RAG_ENABLE_REWRITE",
            default=False, value_type=bool,
        )

        # Task #46 会话记忆: 保留最近 N 轮 (默认 6), 0 = 关闭历史
        self.history_max_turns = self.config.get_effective_value(
            "rag.history_max_turns", env_var="ANYTHING_RAG_HISTORY_MAX_TURNS",
            default=6, value_type=int,
        )

        # Task #49 混合检索 (BM25 + 向量), 默认关 (向后兼容); bm25_retriever 注入了才生效
        self.enable_hybrid_search = self.config.get_effective_value(
            "rag.enable_hybrid_search", env_var="ANYTHING_RAG_ENABLE_HYBRID",
            default=False, value_type=bool,
        )
        self.hybrid_rrf_k = self.config.get_effective_value(
            "rag.hybrid_rrf_k", env_var="ANYTHING_RAG_HYBRID_RRF_K",
            default=60, value_type=int,
        )

        # Phase4 记忆个性化: long_term_memory 注入了才生效 (graceful). 答前注入用户画像 +
        # query 相关 fact ("懂使用者"), 答后从本轮抽 fact 入库 ("越用越懂"). 默认开 (仅当有 memory)。
        self.memory_enabled = bool(long_term_memory is not None and self.config.get_effective_value(
            "rag.memory_enabled", env_var="ANYTHING_RAG_MEMORY_ENABLED",
            default=True, value_type=bool,
        ))
        self.memory_top_k = self.config.get_effective_value(
            "rag.memory_top_k", env_var="ANYTHING_RAG_MEMORY_TOP_K",
            default=3, value_type=int,
        )

        self.logger.info("RAG 模块初始化完成")

    # ============ 会话历史读写 (Task #46) ============

    def _load_history(self, session_id: Optional[str]) -> List[Dict[str, str]]:
        """从 state_store 读最近 N 轮对话, 返回 [{role,content}, ...] 形式.

        - 未注入 state_store / session_id 为空 / history_max_turns=0 时返回 []
        - 只保留 role/content 字段, 不带 timestamp 等元信息 (LLM 不需要)
        - 严格按 N 轮 (= 2N 条 message, user+assistant 各算 1) 截断
        """
        if not session_id or not self.state_store or self.history_max_turns <= 0:
            return []
        try:
            state = self.state_store.get_state(session_id)
        except Exception as e:
            self.logger.warning(f"读会话状态失败 (忽略): session_id={session_id}, err={e}")
            return []
        if not isinstance(state, dict):
            return []
        events = state.get("events") or []
        # 截断到最近 2N 条
        max_msgs = self.history_max_turns * 2
        if len(events) > max_msgs:
            events = events[-max_msgs:]
        messages: List[Dict[str, str]] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            role = ev.get("role")
            content = ev.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content:
                messages.append({"role": role, "content": content})
        return messages

    def _save_turn(
        self,
        session_id: Optional[str],
        user_query: str,
        assistant_answer: str,
        trace_id: Optional[str] = None,
    ) -> None:
        """把本轮 (user + assistant) 各 append 一个 event 到 state_store. 失败仅 WARN."""
        if not session_id or not self.state_store:
            return
        try:
            self.state_store.append_event(session_id, {
                "role": "user", "content": user_query,
                "trace_id": trace_id, "type": "rag",
            })
            self.state_store.append_event(session_id, {
                "role": "assistant", "content": assistant_answer,
                "trace_id": trace_id, "type": "rag",
            })
        except Exception as e:
            self.logger.warning(f"写会话状态失败 (忽略): session_id={session_id}, err={e}")

    # ============ Phase4 记忆个性化 (用户模型 + 跨会话学习) ============
    def _memory_tenant(self, request: Dict[str, Any]) -> str:
        """解析当前 tenant_id (extra_params > observability context > default)."""
        ep = request.get("extra_params") or {}
        tenant = ep.get("tenant_id") or request.get("tenant_id")
        if tenant:
            return str(tenant)
        try:
            from observability_module import get_current_tenant
            cur = get_current_tenant()
            if cur:
                return str(cur)
        except Exception:
            pass
        return "default"

    def _memory_context_block(self, query: str, tenant_id: str) -> str:
        """答前注入: 用户画像 + query 相关 fact, 拼成可加到 prompt 前的上下文块。
        无记忆 / 读失败一律返 '' (fail-open, 不影响 RAG 主流程)。"""
        if not self.memory_enabled or self.long_term_memory is None:
            return ""
        blocks: List[str] = []
        try:
            profile = self.long_term_memory.get_user_profile(tenant_id) or {}
            if profile:
                labels = {"preference": "偏好", "style": "风格", "convention": "约定",
                          "domain": "领域", "weakness": "需主动补位"}
                lines = ["[关于使用者 — 回答时遵循; 标 '需主动补位' 的请主动替 ta cover]"]
                for dim, items in profile.items():
                    for it in items:
                        lines.append(f"- [{labels.get(dim, dim)}] {it}")
                blocks.append("\n".join(lines))
        except Exception as e:
            self.logger.warning(f"[memory] 读用户画像失败 (忽略): tenant={tenant_id}, err={e}")
        try:
            from long_term_memory_module import MemoryQuery
            hits = self.long_term_memory.search_facts(
                MemoryQuery(query=query, tenant_id=tenant_id, top_k=self.memory_top_k)
            )
            if hits:
                lines = ["[已知与本问题相关的信息]"] + [f"- {h.fact.content}" for h in hits]
                blocks.append("\n".join(lines))
        except Exception as e:
            self.logger.warning(f"[memory] 查相关 fact 失败 (忽略): tenant={tenant_id}, err={e}")
        return ("\n\n".join(blocks) + "\n\n") if blocks else ""

    def _learn_from_turn(self, query: str, answer: str, tenant_id: str,
                         session_id: Optional[str]) -> None:
        """答后学习: 从 (query, answer) 抽 fact 入长期记忆 (越用越懂)。best-effort,
        无 LLM 抽取通道 (NotImplementedError) / 失败 都静默跳过, 绝不阻断主响应。"""
        if not self.memory_enabled or self.long_term_memory is None or not answer:
            return
        try:
            facts = self.long_term_memory.extract_facts(
                messages=[{"role": "user", "content": query},
                          {"role": "assistant", "content": answer}],
                tenant_id=tenant_id, session_id=session_id,
            )
        except NotImplementedError:
            return
        except Exception as e:
            self.logger.warning(f"[memory] extract_facts 失败 (忽略): tenant={tenant_id}, err={e}")
            return
        for f in facts or []:
            try:
                self.long_term_memory.add_fact(f)
            except Exception as e:
                self.logger.warning(f"[memory] add_fact 失败 (忽略): err={e}")

    def retrieve(self, request: Dict[str, Any]) -> List[Dict[str, Any]]:
        """执行 chunk 级检索并返回标准结果列表(文档第 12 章 6 步链路)。

        步骤:
            1. Normalize: 清洗/截断/去噪 query
            2. Rewrite (可选): query_rewriter.rewrite(...) -> rewrite_query + filters
            3. Embed: query_embedding = embedding.embed_text(rewrite_query)
            4. Retrieve: vector_db.query(query_embedding, retrieve_k, filters)
            5. Rerank (可选): reranker.rerank(rewrite_query, candidates)
            6. Truncate: 取 top_k
        """
        original_query = self._normalize_query(request.get("query", ""))
        top_k = int(request.get("top_k") or self.top_k_rerank or 5)
        retrieve_k = max(top_k, self.top_k_retrieve)
        trace_id = request.get("trace_id")
        extra_params = request.get("extra_params") or {}
        filters: Dict[str, Any] = dict(extra_params.get("filters") or {})

        self.logger.info(f"RAG 检索开始：trace_id={trace_id}, retrieve_k={retrieve_k}")

        # 2. 可选 query rewrite
        effective_query = original_query
        if self.enable_rewrite and self.query_rewriter is not None and original_query:
            effective_query = self._apply_rewrite(
                query=original_query,
                filters=filters,
                context=extra_params,
                trace_id=trace_id,
            )

        # 3. 向量化 effective_query
        query_embedding = self._embed_query(effective_query, trace_id=trace_id)

        # 3b. P15: KB 过滤下推 — kb_id 的 doc_id 集合在检索阶段就生效 (向量层
        # filters 集合成员判定 + 命中不足自动放大候选; BM25 评分阶段跳过 KB 外
        # chunk)。原先融合后过滤: 大库+小 KB 时全局 top-50 可能全被滤掉 → 0 结果。
        kb_id = extra_params.get("kb_id")
        kb_allowed = self._kb_allowed_doc_ids(kb_id) if kb_id else set()
        if kb_allowed and "doc_id" not in filters:  # 调用方显式 doc_id filter 优先
            filters["doc_id"] = sorted(kb_allowed)

        # 4. 检索向量库
        raw_items: List[Dict[str, Any]] = []
        if self.vector_db is not None and query_embedding is not None:
            raw_items = self._query_vector_db(
                query_embedding=query_embedding,
                top_k=retrieve_k,
                filters=filters or None,
                trace_id=trace_id,
            )

        # 5. 统一 chunk 结构
        vec_chunks = [self._normalize_retrieved_item(item) for item in raw_items]
        vec_chunks = [c for c in vec_chunks if c is not None]

        # 5b. Task #49 BM25 关键字稀疏召回 (混合检索开关 + retriever 都满足时触发)
        bm25_chunks: List[Dict[str, Any]] = []
        hybrid_active = (
            self.enable_hybrid_search
            and self.bm25_retriever is not None
            and getattr(self.bm25_retriever, "size", 0) > 0
        )
        if hybrid_active:
            bm25_chunks = self._query_bm25(
                query_text=effective_query, top_k=retrieve_k, trace_id=trace_id,
                allowed_doc_ids=kb_allowed or None,
            )

        # 5c. 融合 (RRF) - 仅在混合模式且 BM25 有结果时
        if hybrid_active and bm25_chunks:
            chunks = self._hybrid_merge(
                vec_chunks=vec_chunks, bm25_chunks=bm25_chunks, trace_id=trace_id,
            )
        else:
            chunks = vec_chunks

        # 5d. ZZ-3 KB 过滤安全网 — 正常已在 3b 下推到检索阶段, 这里兜底
        # (防调用方显式 doc_id filter 与 KB 并存等边角漏过)
        if kb_allowed:
            before = len(chunks)
            chunks = [c for c in chunks if str(c.get("doc_id")) in kb_allowed]
            if len(chunks) != before:
                self.logger.info(
                    f"RAG KB 过滤(安全网): kb_id={kb_id}, {before}→{len(chunks)} chunks"
                )

        # 6. 可选 rerank (用 effective_query 而非 original)
        if self.enable_rerank and self.reranker is not None and chunks:
            chunks = self._apply_rerank(query=effective_query, chunks=chunks, trace_id=trace_id)

        # 7. 按 top_k 截断为生成候选
        final_chunks = chunks[:top_k] if top_k > 0 else chunks

        self.logger.info(
            f"RAG 检索完成：trace_id={trace_id}, retrieved={len(chunks)}, selected={len(final_chunks)}"
        )
        return final_chunks

    def _kb_allowed_doc_ids(self, kb_id):
        """ZZ-3: 读知识库的 doc_id 集合, 用于检索过滤.

        MVP 实现: 直接读 run/kb.sqlite3 (application 层 kb router 管理的库).
        理想架构应由 application 层把 kb_id 解析成 doc_ids 注入 extra_params,
        RAG 不直接依赖 kb 库 — 留作后续重构. 读失败 / KB 空时返回空集 (不过滤).
        P15: 加 5s TTL 缓存 — 原先每次查询现开一个 sqlite 连接。
        """
        ent = self._kb_cache.get(str(kb_id))
        now = time.time()
        if ent is not None and ent[1] > now:
            return ent[0]
        allowed = self._kb_allowed_doc_ids_uncached(kb_id)
        if len(self._kb_cache) >= 32:
            self._kb_cache.clear()
        self._kb_cache[str(kb_id)] = (allowed, now + 5.0)
        return allowed

    def _kb_allowed_doc_ids_uncached(self, kb_id):
        try:
            import sqlite3
            import os
            from pathlib import Path
            p = Path(os.environ.get("ANYTHING_DATA_ROOT", "run")) / "kb.sqlite3"
            if not p.exists():
                return set()
            conn = sqlite3.connect(str(p), timeout=3.0)
            try:
                rows = conn.execute(
                    "SELECT doc_id FROM kb_doc WHERE kb_id = ?", (str(kb_id),)
                ).fetchall()
            finally:
                conn.close()
            return {str(r[0]) for r in rows if r and r[0]}
        except Exception as e:
            self.logger.warning(f"读 KB doc_ids 失败 (忽略, 不过滤): kb_id={kb_id}, err={e}")
            return set()

    def _apply_rewrite(
        self,
        query: str,
        filters: Dict[str, Any],
        context: Dict[str, Any],
        trace_id: Optional[str],
    ) -> str:
        """执行查询改写,失败时退化为原 query。

        改写产出的 filters 会**合并**进调用方传入的 filters(调用方传入的优先,
        改写补充未指定字段)。
        """
        try:
            if hasattr(self.query_rewriter, "should_rewrite"):
                if not self.query_rewriter.should_rewrite(query):
                    return query
            result = self.query_rewriter.rewrite(query, context=context)
        except Exception as e:
            self.logger.warning(
                f"Query rewrite 失败,退化为原 query: trace_id={trace_id}, error={str(e)}"
            )
            return query

        if result is None:
            return query

        rewrite_query = getattr(result, "rewrite_query", None) or query
        rewrite_filters = getattr(result, "filters", None) or {}

        # 合并 filters: 调用方传入的优先(已在 filters 中),改写仅补未指定字段
        for k, v in rewrite_filters.items():
            filters.setdefault(k, v)

        if rewrite_query != query:
            self.logger.info(
                f"Query rewritten: trace_id={trace_id}, "
                f"original={query[:50]!r}, rewrite={rewrite_query[:50]!r}"
            )
        return rewrite_query

    def generate(self, request: Dict[str, Any], retrieved_chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """基于 chunk 级检索结果生成回答与 citations"""
        query = self._normalize_query(request.get("query", ""))
        trace_id = request.get("trace_id")
        session_id = request.get("session_id")
        tenant_id = self._memory_tenant(request)  # Phase4 记忆个性化

        context_chunks = self._assemble_context(retrieved_chunks)
        # A1: 检索 0 结果 → 不调 LLM, 直接诚实回复 (杜绝无依据幻觉 + 省一次 LLM 调用).
        if not context_chunks:
            no_ctx = "根据现有知识库, 没有找到与该问题相关的内容。可以换个说法, 或先上传相关文档再问。"
            self._save_turn(session_id=session_id, user_query=query,
                            assistant_answer=no_ctx, trace_id=trace_id)
            # Phase4: 即便无文档命中也从本轮抽 fact (用户陈述/偏好), 让"越用越懂"不漏掉日常对话
            self._learn_from_turn(query, no_ctx, tenant_id, session_id)
            self.logger.info(f"RAG 检索 0 结果, 返回诚实兜底 (不调 LLM): trace_id={trace_id}")
            return {"answer": no_ctx, "citations": []}
        # Task #46: 注入历史
        history = self._load_history(session_id)
        prompt = self._build_prompt(query=query, context_chunks=context_chunks, history=history)
        # Phase4: 答前注入用户画像 + query 相关 fact (懂使用者); 无记忆则空, prompt 不变
        prompt = self._memory_context_block(query, tenant_id) + prompt

        self.logger.info(
            f"RAG 生成开始：trace_id={trace_id}, context_chunks={len(context_chunks)}, "
            f"history_messages={len(history)}"
        )

        answer_text = self._call_llm_generate(prompt=prompt, trace_id=trace_id)
        citations = self._build_citations(context_chunks)

        # 若模型未输出引用标记，则在答案后补最基础引用
        answer_text = self._ensure_citation_marker(answer_text, citations)

        # Task #46: 落 session 历史
        self._save_turn(
            session_id=session_id,
            user_query=query,
            assistant_answer=answer_text,
            trace_id=trace_id,
        )
        # Phase4: 答后从本轮抽 fact 入长期记忆 (越用越懂); best-effort
        self._learn_from_turn(query, answer_text, tenant_id, session_id)

        self.logger.info(f"RAG 生成完成：trace_id={trace_id}")
        return {
            "answer": answer_text,
            "citations": citations,
        }

    def run_stream(self, request: Dict[str, Any]):
        """RAG 真实流式 generator (Task #44). yield event dict 序列:

        Event 类型:
          {type: 'meta',     retrieved_chunks: [...], citations: [...]}     检索完成
          {type: 'chunk',    text: str}                                      LLM token 增量 (多次)
          {type: 'done',     cost_time: float, answer_length: int}           完成
          {type: 'error',    code: str, message: str}                        异常

        实现:
          1. 检索 + rerank 同步 (跟 run() 一样)
          2. yield 一次 meta event (含 chunks + citations, 不含 answer)
          3. 调 llm_client.chat_stream(prompt), 边收 token 边 yield chunk
          4. yield done event

        WS endpoint 把 generator 的 event 序列转发即可,
        无需后端再做 simulated 切片,首字符延迟 = LLM TTFT (time to first token)。
        """
        start_time = time.time()
        trace_id = request.get("trace_id")

        try:
            # 1. 检索
            with trace_span(
                "rag.retrieve",
                attributes={
                    "anything.trace_id": trace_id or "-",
                    "rag.top_k": int(request.get("top_k") or 0),
                },
            ) as span:
                retrieved_chunks = self.retrieve(request)
                span.set_attribute("rag.retrieved_count", len(retrieved_chunks))

            # 2. 拼上下文 + prompt + citations (跟 generate() 同样)
            query = self._normalize_query(request.get("query", ""))
            session_id = request.get("session_id")
            tenant_id = self._memory_tenant(request)  # Phase4 记忆个性化
            context_chunks = self._assemble_context(retrieved_chunks)
            history = self._load_history(session_id)  # Task #46
            prompt = self._build_prompt(query=query, context_chunks=context_chunks, history=history)
            prompt = self._memory_context_block(query, tenant_id) + prompt  # Phase4: 答前注入
            citations = self._build_citations(context_chunks)

            self.logger.info(
                f"RAG run_stream: trace_id={trace_id}, "
                f"context_chunks={len(context_chunks)}, "
                f"history_messages={len(history)}, "
                f"prompt_length={len(prompt)}"
            )

            # 3. yield meta event — 检索结果先到前端, 用户立刻看到 citations
            yield {
                "type": "meta",
                "retrieved_chunks": [
                    {
                        "chunk_id": item.get("chunk_id"),
                        "doc_id": item.get("doc_id"),
                        "file_name": item.get("file_name"),
                        "chunk_index": item.get("chunk_index"),
                        "score": item.get("score"),
                        # ZZ-7: 补 content + start/end_char — 之前漏传, 导致前端检索结果
                        # 列表显示"无 content"、"查看原文"抠不到正确片段 (溯源链路断在数据层).
                        "content": item.get("content"),
                        "start_char": item.get("start_char"),
                        "end_char": item.get("end_char"),
                        # 可观察性: 透传 rerank_source 让调用方知道 score 是
                        # 来自 vector_db 原始相似度还是 rerank 加工后的分数
                        "rerank_source": item.get("rerank_source"),
                        # Task #49: 混合检索时透传 RRF 融合分数
                        "rrf_score": item.get("rrf_score"),
                    }
                    for item in retrieved_chunks
                ],
                "citations": citations,
            }

            # A1: 流式 RAG 检索 0 结果 → 直接诚实回复 + done, 不调 LLM (与非流式对称, 杜绝幻觉)
            if not context_chunks:
                no_ctx = "根据现有知识库, 没有找到与该问题相关的内容。可以换个说法, 或先上传相关文档再问。"
                yield {"type": "chunk", "text": no_ctx}
                self._save_turn(session_id=session_id, user_query=query,
                                assistant_answer=no_ctx, trace_id=trace_id)
                # Phase4: 无文档命中也学习本轮 (与非流式对称)
                self._learn_from_turn(query, no_ctx, tenant_id, session_id)
                yield {"type": "done", "cost_time": round(time.time() - start_time, 3),
                       "answer_length": len(no_ctx), "code": "SUCCESS"}
                return

            # 4. LLM 真实流式
            with trace_span(
                "rag.generate_stream",
                attributes={"anything.trace_id": trace_id or "-"},
            ):
                answer_buffer = []
                # 若 llm_client 暴露了 chat_stream, 走真实 SSE;
                # 否则降级为 _call_llm_generate 一次拿完整文本作为单 chunk yield
                if hasattr(self.llm_client, "chat_stream"):
                    try:
                        for token in self.llm_client.chat_stream(
                            prompt=prompt, trace_id=trace_id
                        ):
                            if token:
                                answer_buffer.append(token)
                                yield {"type": "chunk", "text": token}
                    except Exception as e:
                        # 流式失败 -> 降级一次性
                        self.logger.warning(
                            f"chat_stream 失败, 降级 sync: trace_id={trace_id}, err={e}"
                        )
                        full = self._call_llm_generate(prompt=prompt, trace_id=trace_id)
                        if full:
                            answer_buffer.append(full)
                            yield {"type": "chunk", "text": full}
                else:
                    # adapter 完全没 stream 能力 -> 同步生成 + 一次 yield
                    full = self._call_llm_generate(prompt=prompt, trace_id=trace_id)
                    if full:
                        answer_buffer.append(full)
                        yield {"type": "chunk", "text": full}

            full_answer = "".join(answer_buffer)
            full_answer = self._ensure_citation_marker(full_answer, citations)

            # Task #46: 流式完成后落 session 历史
            self._save_turn(
                session_id=session_id,
                user_query=query,
                assistant_answer=full_answer,
                trace_id=trace_id,
            )
            # Phase4: 答后从本轮抽 fact 入长期记忆 (越用越懂); best-effort
            self._learn_from_turn(query, full_answer, tenant_id, session_id)

            # 5. 完成事件
            yield {
                "type": "done",
                "cost_time": round(time.time() - start_time, 3),
                "answer_length": len(full_answer),
                "code": "SUCCESS",
            }

        except Exception as e:
            self.logger.error(f"RAG run_stream 异常: {e}, trace_id={trace_id}")
            # "先存后端": 流式检索/LLM 失败也要落盘本轮 (与非流式 run() 对称),
            # 否则刷新后用户提问丢失. 走到这里 _save_turn(469) 未执行, 不会重复.
            self._save_turn(
                session_id=request.get("session_id"),
                user_query=self._normalize_query(request.get("query", "")),
                assistant_answer="⚠️ 本轮回答生成失败（服务暂时不可用），你的问题已保留，可稍后重试。",
                trace_id=trace_id,
            )
            yield {
                "type": "error",
                "code": "RAG_RUN_FAILED",
                "message": str(e),
            }

    def run(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """执行完整 RAG 流程"""
        start_time = time.time()
        trace_id = request.get("trace_id")

        try:
            self.logger.info(
                f"RAG 执行开始：trace_id={trace_id}, top_k={request.get('top_k')}, session_id={request.get('session_id')}"
            )

            with trace_span(
                "rag.retrieve",
                attributes={
                    "anything.trace_id": trace_id or "-",
                    "rag.top_k": int(request.get("top_k") or 0),
                    "rag.enable_rerank": self.enable_rerank,
                    "rag.enable_rewrite": self.enable_rewrite,
                },
            ) as span:
                retrieved_chunks = self.retrieve(request)
                span.set_attribute("rag.retrieved_count", len(retrieved_chunks))

            with trace_span(
                "rag.generate",
                attributes={"anything.trace_id": trace_id or "-"},
            ) as span:
                generated = self.generate(request, retrieved_chunks)
                span.set_attribute("rag.citations_count", len(generated.get("citations", [])))

            return {
                "code": "SUCCESS",
                "message": "ok",
                "data": {
                    "answer": generated.get("answer", ""),
                    "citations": generated.get("citations", []),
                    "retrieved_chunks": [
                        {
                            "chunk_id": item.get("chunk_id"),
                            "doc_id": item.get("doc_id"),
                            "file_name": item.get("file_name"),
                            "chunk_index": item.get("chunk_index"),
                            "score": item.get("score"),
                            # ZZ-7: 补 content + start/end_char (同流式分支, 溯源链路完整)
                            "content": item.get("content"),
                            "start_char": item.get("start_char"),
                            "end_char": item.get("end_char"),
                            # 可观察性: 透传 rerank_source 让调用方知道 score 是
                            # 来自 vector_db 原始相似度还是 rerank 加工后的分数
                            "rerank_source": item.get("rerank_source"),
                            # Task #49: 混合检索时透传 RRF 融合分数
                            "rrf_score": item.get("rrf_score"),
                        }
                        for item in retrieved_chunks
                    ],
                },
                "trace_id": trace_id,
                "retryable": False,
                "details": None,
                "cost_time": round(time.time() - start_time, 3),
            }

        except Exception as e:
            self.logger.error(f"RAG 执行异常：{str(e)}, trace_id={trace_id}")
            # "先存后端": 检索/LLM 失败也要把本轮落盘, 否则刷新后用户的提问彻底丢失.
            # _save_turn 自带 try/except (失败仅 WARN), 不会再抛. 成功路径已在 generate() 存过,
            # 走到这里说明 generate 抛在 save 之前, 不会重复.
            self._save_turn(
                session_id=request.get("session_id"),
                user_query=self._normalize_query(request.get("query", "")),
                assistant_answer="⚠️ 本轮回答生成失败（服务暂时不可用），你的问题已保留，可稍后重试。",
                trace_id=trace_id,
            )
            return self._handle_exception(exception=e, trace_id=trace_id, request=request)

    def _normalize_query(self, query: str) -> str:
        """最小 query 规范化"""
        if query is None:
            return ""
        normalized = str(query).strip().replace("\r\n", "\n").replace("\r", "\n")
        if len(normalized) > 4000:
            normalized = normalized[:4000]
        return normalized

    def _embed_query(self, query: str, trace_id: Optional[str]) -> Optional[List[float]]:
        """对 query 做向量化，兼容多种 embedding 接口"""
        if not query:
            return None

        if self.embedding is None:
            return None

        try:
            if hasattr(self.embedding, "embed_text"):
                try:
                    result = self.embedding.embed_text(query, trace_id=trace_id)
                except TypeError:
                    result = self.embedding.embed_text(query)

                if isinstance(result, dict):
                    data = result.get("data")
                    if isinstance(data, dict):
                        items = data.get("items") or []
                        if items:
                            return items[0].get("embedding")
                    if "embedding" in result:
                        return result.get("embedding")
                if isinstance(result, list):
                    return result

            if hasattr(self.embedding, "embed_texts"):
                try:
                    result = self.embedding.embed_texts([query], trace_id=trace_id)
                except TypeError:
                    result = self.embedding.embed_texts([query])

                if isinstance(result, dict):
                    data = result.get("data")
                    if isinstance(data, dict):
                        items = data.get("items") or []
                        if items:
                            return items[0].get("embedding")
                if isinstance(result, list) and result:
                    return result[0]

        except Exception as e:
            self.logger.warning(f"Query 向量化失败：trace_id={trace_id}, error={str(e)}")
            raise RuntimeError("向量化失败") from e

        return None

    def _query_vector_db(
            self,
            query_embedding: List[float],
            top_k: int,
            filters: Optional[Dict[str, Any]],
            trace_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        """按 BaseVectorDB.query 抽象契约调用向量库检索。

        契约（见 data_layer/vector_db_module/core/base.py:BaseVectorDB.query）：
            query(query_vector: List[float], top_k: int = 5, filters: Optional[Dict] = None) -> List[Dict]
        """
        if self.vector_db is None:
            return []

        try:
            result = self.vector_db.query(
                query_vector=query_embedding,
                top_k=top_k,
                filters=filters,
            )
        except Exception as e:
            self.logger.warning(f"向量检索失败：trace_id={trace_id}, error={str(e)}")
            raise RuntimeError("向量检索失败") from e

        # BaseVectorDB.query 契约返回 List[Dict]
        if isinstance(result, list):
            return result

        # 兜底：极少数旧实现可能返回 {"data": [...]} 或 {"items": [...]}，保留最小兼容
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, list):
                return data
            items = result.get("items")
            if isinstance(items, list):
                return items

        return []

    # ============ Task #49 BM25 + RRF 融合 ============

    def _query_bm25(
        self,
        query_text: str,
        top_k: int,
        trace_id: Optional[str],
        allowed_doc_ids=None,
    ) -> List[Dict[str, Any]]:
        """调 bm25_retriever.query() 返回 chunk 列表; 失败仅 WARN 不抛.

        P15: allowed_doc_ids 下推到评分阶段过滤。
        P8: 结果统一过 _normalize_retrieved_item — BM25 meta 不再带 content,
        normalize 会按偏移从 doc_store 取文 (旧索引带 content 时直用)。
        """
        if self.bm25_retriever is None or not query_text:
            return []
        try:
            hits = self.bm25_retriever.query(
                query_text=query_text, top_k=top_k, allowed_doc_ids=allowed_doc_ids,
            )
        except TypeError:
            # 老签名 retriever (无 allowed_doc_ids) 兼容
            hits = self.bm25_retriever.query(query_text=query_text, top_k=top_k)
        except Exception as e:
            self.logger.warning(f"BM25 检索失败 (忽略): trace_id={trace_id}, err={e}")
            return []
        normalized = [self._normalize_retrieved_item(h) for h in (hits or [])]
        return [c for c in normalized if c is not None]

    def _hybrid_merge(
        self,
        vec_chunks: List[Dict[str, Any]],
        bm25_chunks: List[Dict[str, Any]],
        trace_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        """RRF 融合 vec + bm25 两路 chunk 列表, 用 rag_module.extensions.rrf_merge."""
        from rag_module.extensions import rrf_merge
        merged = rrf_merge(
            rank_lists=[vec_chunks, bm25_chunks],
            k=self.hybrid_rrf_k,
            id_field="chunk_id",
        )
        self.logger.info(
            f"混合检索融合完成: trace_id={trace_id}, vec={len(vec_chunks)}, "
            f"bm25={len(bm25_chunks)}, fused={len(merged)}"
        )
        return merged

    def _normalize_retrieved_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """将向量库结果标准化为 chunk 级结构"""
        if not isinstance(item, dict):
            return None

        metadata = item.get("metadata") or {}
        chunk_id = (
            item.get("chunk_id")
            or metadata.get("chunk_id")
            or item.get("vector_id")
        )
        doc_id = item.get("doc_id") or metadata.get("doc_id")
        file_name = item.get("file_name") or metadata.get("file_name")
        chunk_index = item.get("chunk_index", metadata.get("chunk_index"))
        content = (
            item.get("content")
            or metadata.get("content")
            or self._try_resolve_content_from_doc_store(metadata)
        )
        score = item.get("score", item.get("distance"))

        if not chunk_id or not doc_id:
            return None

        return {
            "chunk_id": chunk_id,
            "doc_id": doc_id,
            "file_name": file_name,
            "chunk_index": chunk_index,
            "content": content or "",
            "score": score,
            "start_char": item.get("start_char", metadata.get("start_char")),
            "end_char": item.get("end_char", metadata.get("end_char")),
        }

    def _try_resolve_content_from_doc_store(self, metadata: Dict[str, Any]) -> str:
        """P8 取文主路径: meta 不再存全文, 命中后按 doc_id + start/end_char 从
        document_store 抠原文 (正文唯一权威在 doc 文件)。

        - 旧索引 meta 仍带 content 时不会走到这里 (normalize 优先用 meta.content)
        - 带 60s TTL 的小缓存: 一次查询的多个 chunk 通常落在同一批 doc 上
        - 任何失败返回 "" (上游已有空 content 容忍逻辑)
        """
        if self.doc_store is None:
            return ""
        doc_id = metadata.get("doc_id")
        if not doc_id:
            return ""
        content = self._get_doc_content_cached(str(doc_id))
        if not content:
            return ""
        try:
            start = int(metadata.get("start_char"))
            end = int(metadata.get("end_char"))
        except (TypeError, ValueError):
            start, end = -1, -1
        if 0 <= start < end <= len(content):
            return content[start:end]
        # 偏移缺失/越界 (文档被 update 过等): 退化取头部, 长度对齐单 chunk 上限
        return content[: self.max_chunk_in_prompt_tokens * 4]

    def _get_doc_content_cached(self, doc_id: str) -> str:
        ent = self._doc_content_cache.get(doc_id)
        now = time.time()
        if ent is not None and ent[1] > now:
            return ent[0]
        content = ""
        try:
            doc = self.doc_store.get_document(doc_id)
            if isinstance(doc, dict):
                content = doc.get("content") or ""
        except Exception as e:
            self.logger.warning(f"RAG 取文失败 (chunk 将无正文): doc_id={doc_id}, err={e}")
        if len(self._doc_content_cache) >= 64:
            self._doc_content_cache.clear()  # 简单上界, 防长期驻留膨胀
        self._doc_content_cache[doc_id] = (content, now + 60.0)
        return content

    def _apply_rerank(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        trace_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        """按 BaseReranker.rerank(query, candidates, top_k) 契约调用,失败时退化原顺序。"""
        try:
            result = self.reranker.rerank(query, chunks, top_k=self.top_k_rerank)
            if isinstance(result, list) and result:
                return result[: self.top_k_rerank]
        except Exception as e:
            self.logger.warning(f"Rerank 失败，退回原检索顺序：trace_id={trace_id}, error={str(e)}")
        return chunks[: self.top_k_rerank]

    def _assemble_context(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按 chunk 级拼装上下文，限制总长度与单 chunk 长度"""
        selected = []
        total_chars = 0
        max_context_chars = self.max_context_tokens * 4
        max_chunk_chars = self.max_chunk_in_prompt_tokens * 4

        seen_chunk_ids = set()
        for chunk in chunks:
            chunk_id = chunk.get("chunk_id")
            if not chunk_id or chunk_id in seen_chunk_ids:
                continue

            text = (chunk.get("content") or "").strip()
            if not text:
                continue

            if len(text) > max_chunk_chars:
                text = text[:max_chunk_chars]

            if total_chars + len(text) > max_context_chars:
                break

            copied = dict(chunk)
            copied["content"] = text
            selected.append(copied)
            seen_chunk_ids.add(chunk_id)
            total_chars += len(text)

        return selected

    def _build_prompt(
        self,
        query: str,
        context_chunks: List[Dict[str, Any]],
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """构建最小可运行 prompt, 可选携带会话历史 (Task #46).

        history: [{role:"user"|"assistant", content:str}, ...] 按时间顺序, 已经截断到 N 轮。
        """
        context_parts = []
        for idx, chunk in enumerate(context_chunks, start=1):
            # A2: 用序号 [1][2] 标资料 (对应 citations 顺序), 让 LLM 引用时输出 [1][2]
            # 而非裸 chunk_id UUID; 前端把 [N] 渲染成可点击角标跳对应来源.
            context_parts.append(f"[{idx}] {chunk.get('content', '')}")
        context_text = "\n\n".join(context_parts)

        history_block = ""
        if history:
            lines = []
            for m in history:
                tag = "用户" if m.get("role") == "user" else "助手"
                lines.append(f"{tag}: {m.get('content','')}")
            history_block = "\n\n=== 历史对话 (最近几轮, 仅供理解上下文) ===\n" + "\n".join(lines) + "\n=== 历史结束 ===\n\n"

        # Task U (#55): 顶部注入 ProjectMemory (AGENTS.md / CLAUDE.md), 让 RAG
        # 答案符合项目约定/语言/术语. 失败仅 WARN 不中断主链路.
        memory_block = ""
        try:
            mem = get_project_memory().load()
            if mem:
                memory_block = (
                    f"<ProjectMemory>\n{mem.strip()}\n</ProjectMemory>\n\n"
                )
        except Exception as e:
            self.logger.warning(f"[rag] 加载 ProjectMemory 失败 (忽略): {e}")

        prompt = (
            f"{memory_block}"
            "你是一个严格基于「知识片段」回答问题的助手。请遵守:\n"
            "1. 只依据下面提供的上下文回答, 不得使用上下文之外的知识, 不得编造.\n"
            "2. 若上下文中没有与问题相关的信息, 必须直接回答: "
            "「根据现有知识库, 没有找到与该问题相关的内容。」并停止, 不要编造答案.\n"
            "3. 若回答用到某条资料, 在该句末尾用 [序号] 标注 (序号对应上面资料的 [1][2] 编号), 方便溯源.\n"
            "如果用户问题涉及之前对话内容, 可参考下面的历史。\n"
            f"{history_block}"
            f"当前问题: {query}\n\n"
            f"上下文:\n{context_text or '(无相关上下文)'}\n\n"
            "请给出中文回答。"
        )
        return prompt

    def _call_llm_generate(self, prompt: str, trace_id: Optional[str]) -> str:
        """调用 LLM 生成回答，统一走兼容适配层"""
        try:
            text = call_llm_compat(
                llm_client=self.llm_client,
                prompt=prompt,
                trace_id=trace_id,
            )
            return text or "模型未返回有效内容。"
        except Exception as e:
            self.logger.warning(f"LLM 生成失败：trace_id={trace_id}, error={str(e)}")
            return "模型未返回有效内容。"

    def _build_citations(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """构建结构化 citations"""
        citations = []
        for chunk in chunks:
            citations.append(
                {
                    "chunk_id": chunk.get("chunk_id"),
                    "doc_id": chunk.get("doc_id"),
                    "file_name": chunk.get("file_name"),
                    "start_char": chunk.get("start_char"),
                    "end_char": chunk.get("end_char"),
                    "score": chunk.get("score"),
                }
            )
        return citations

    def _ensure_citation_marker(self, answer_text: str, citations: List[Dict[str, Any]]) -> str:
        """A2: 不再往正文硬塞 [CIT:chunk_id] 裸标记 (前端不识别 + 露出丑陋 UUID).

        溯源靠 citations 数组 → 前端右栏 chunk 列表 + 答案下方 citation chip;
        LLM 若按 prompt 用 [1][2] 编号引用, 原样保留 (前端渲染成可点击角标).
        历史遗留的 [CIT:...] 也清掉, 避免老答案残留丑标记.
        """
        import re as _re
        text = (answer_text or "").strip()
        text = _re.sub(r"\s*\[CIT:[^\]]+\]", "", text).strip()
        return text

    def _handle_exception(
        self,
        exception: Exception,
        trace_id: Optional[str],
        request: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """统一异常处理(委托给 deps_module.handle_exception_to_envelope)。"""
        return handle_exception_to_envelope(
            exception_handler=self.exception_handler,
            exception=exception,
            trace_id=trace_id,
            fallback_code="RAG_RUN_FAILED",
            fallback_message="RAG执行失败",
            stage="rag",
            context={
                "query": (request or {}).get("query"),
                "top_k": (request or {}).get("top_k"),
            },
            retryable_codes={
                "VECTOR_QUERY_FAILED",
                "EMBEDDING_INIT_FAILED",
                "RAG_RUN_FAILED",
            },
        )

    def call_rag(
        self,
        query: str,
        top_k: int = 5,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        兼容旧抽象接口：将旧风格调用转成统一 request dict，再复用 run()
        """
        request = {
            "type": "rag",
            "query": query,
            "top_k": top_k,
            "trace_id": trace_id,
            "extra_params": extra_params or {},
        }

        if session_id:
            request["session_id"] = session_id

        return self.run(request)
