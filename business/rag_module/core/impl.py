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

        self.logger.info("RAG 模块初始化完成")

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
        chunks = [self._normalize_retrieved_item(item) for item in raw_items]
        chunks = [c for c in chunks if c is not None]

        # 6. 可选 rerank (用 effective_query 而非 original)
        if self.enable_rerank and self.reranker is not None and chunks:
            chunks = self._apply_rerank(query=effective_query, chunks=chunks, trace_id=trace_id)

        # 7. 按 top_k 截断为生成候选
        final_chunks = chunks[:top_k] if top_k > 0 else chunks

        self.logger.info(
            f"RAG 检索完成：trace_id={trace_id}, retrieved={len(chunks)}, selected={len(final_chunks)}"
        )
        return final_chunks

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

        context_chunks = self._assemble_context(retrieved_chunks)
        prompt = self._build_prompt(query=query, context_chunks=context_chunks)

        self.logger.info(
            f"RAG 生成开始：trace_id={trace_id}, context_chunks={len(context_chunks)}"
        )

        answer_text = self._call_llm_generate(prompt=prompt, trace_id=trace_id)
        citations = self._build_citations(context_chunks)

        # 若模型未输出引用标记，则在答案后补最基础引用
        answer_text = self._ensure_citation_marker(answer_text, citations)

        self.logger.info(f"RAG 生成完成：trace_id={trace_id}")
        return {
            "answer": answer_text,
            "citations": citations,
        }

    def run(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """执行完整 RAG 流程"""
        start_time = time.time()
        trace_id = request.get("trace_id")

        try:
            self.logger.info(
                f"RAG 执行开始：trace_id={trace_id}, top_k={request.get('top_k')}, session_id={request.get('session_id')}"
            )

            retrieved_chunks = self.retrieve(request)
            generated = self.generate(request, retrieved_chunks)

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
        """兼容性兜底：若向量库未带 content，尝试从文档存储解析。
        注意：这里只做兼容，不作为整篇文档主路径。
        """
        if self.doc_store is None:
            return ""

        # 当前阶段不建议整篇回填；这里只返回空，避免走错路径。
        return ""

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

    def _build_prompt(self, query: str, context_chunks: List[Dict[str, Any]]) -> str:
        """构建最小可运行 prompt"""
        context_parts = []
        for chunk in context_chunks:
            context_parts.append(
                f"[{chunk.get('chunk_id')}] {chunk.get('content', '')}"
            )

        context_text = "\n\n".join(context_parts)
        prompt = (
            "你是一个基于知识片段回答问题的助手。\n"
            "请严格依据提供的上下文回答，并尽量保留引用标记。\n\n"
            f"问题：{query}\n\n"
            f"上下文：\n{context_text}\n\n"
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
        """确保 answer 中至少带一个引用标记"""
        if not answer_text:
            answer_text = ""
        if "[CIT:" in answer_text:
            return answer_text

        if citations:
            marker = citations[0].get("chunk_id")
            if marker:
                return f"{answer_text}\n[CIT:{marker}]".strip()
        return answer_text.strip()

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
