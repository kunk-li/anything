# -*- coding: utf-8 -*-
"""
RagRetrievalMixin (从 impl.py 拆出 — 检索机制, 零行为变更)

    query 规范化/向量化 → 向量库 + BM25 检索 → RRF 融合 → 标准化/取文 → rerank。

依赖 SimpleRAG (self): embedding, vector_db, bm25_retriever, hybrid_rrf_k,
    doc_store, max_chunk_in_prompt_tokens, _doc_content_cache, reranker, top_k_rerank, logger
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


class RagRetrievalMixin:
    """query 规范化/向量化/检索/融合/取文/rerank。"""

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
