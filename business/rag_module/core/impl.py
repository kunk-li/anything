# -*- coding: utf-8 -*-
"""
RAG 模块具体实现类
实现完整 RAG 全流程，串联所有依赖模块，是系统默认使用的 RAG 实现类
"""

import time
from typing import List, Dict, Optional

from .base import BaseRAG
from ..model.data_model import RAGRequest, RAGResponse, RetrievedChunk
from ..prompt.prompt_template import RAG_PROMPT_TEMPLATE
from ..utils.tool_functions import assemble_contexts

# 依赖模块导入（遵循设计文档依赖关系）
from embedding_module.core.impl import STEmbedding
from vector_db_module.core.impl import FaissVectorDB
from document_store_module.core.impl import LocalDocumentStore
from common_utils_module.core.impl import CommonUtils
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import RAGException


class SimpleRAG(BaseRAG):
    """标准 RAG 实现类：串联检索 + 生成全流程，系统默认实现"""

    def __init__(self, llm_client):
        """
        初始化 RAG 模块，注入大模型客户端，加载系统配置
        :param llm_client: 大模型客户端（由外部注入，解耦依赖，需具备 generate 方法）
        """
        # 基础支撑层初始化
        self.utils = CommonUtils()
        self.logger = SystemLogger()
        self.config = ConfigManager()
        self.config.load_config()

        # 核心依赖模块初始化（默认实现，生产环境可通过配置替换）
        self.embedding = STEmbedding()  # 向量生成模块
        self.vector_db = FaissVectorDB()  # 向量数据库模块
        self.doc_store = LocalDocumentStore()  # 文档存储模块
        self.llm = llm_client  # 大模型客户端（外部注入）

        # 读取系统 RAG 核心配置
        self.default_top_k = self.config.get_config("rag.default_top_k", 5)
        self.max_context_length = self.config.get_config("rag.max_context_length", 4096)
        self.context_truncate_length = self.config.get_config("rag.context_truncate_length", 1200)

        self.logger.info("RAG 模块初始化完成，加载系统默认配置")

    def retrieve(self, query: str, top_k: int = 5) -> List[Dict]:
        """实现抽象方法：检索步骤，问题向量化 + 向量检索 + 结果格式化"""
        try:
            # 1. 问题向量化
            query_vector = self.embedding.embed_text(query)

            # 2. 向量检索
            hits = self.vector_db.query(query_vector, top_k=top_k)

            # 3. 结果格式化
            results = []
            for h in hits:
                meta = h.get("metadata", {})
                results.append({
                    "vector_id": h.get("vector_id"),
                    "score": h.get("score"),
                    "doc_id": meta.get("doc_id"),
                    "chunk_id": meta.get("chunk_id", ""),
                    "file_name": meta.get("file_name", ""),
                    "metadata": meta
                })
            return results
        except Exception as e:
            self.logger.error(f"RAG 检索失败：{str(e)}")
            raise RAGException("RAG_RUN_FAILED", f"检索步骤失败：{str(e)}")

    def generate(self, query: str, contexts: List[str]) -> str:
        """实现抽象方法：生成步骤，上下文拼接+Prompt 渲染 + 大模型生成"""
        try:
            # 1. 上下文拼接
            context_text = assemble_contexts(contexts)

            # 2. Prompt 渲染
            prompt = RAG_PROMPT_TEMPLATE.format(context=context_text, query=query)

            # 3. 大模型生成
            # 假设注入的 llm_client 具备 generate 方法，符合架构设计 5.1.1
            answer = self.llm.generate(prompt)

            return answer
        except Exception as e:
            self.logger.error(f"RAG 生成失败：{str(e)}")
            raise RAGException("RAG_RUN_FAILED", f"生成步骤失败：{str(e)}")

    def run(self, query: str, top_k: int = 5) -> Dict:
        """实现抽象方法：RAG 全流程执行，检索→获取原文→生成→结果封装"""
        start_time = time.time()
        try:
            # 1. 参数校验
            if not query or not query.strip():
                raise RAGException("PARAM_MISSING", "用户问题不能为空")

            # 2. 检索
            retrieved = self.retrieve(query, top_k=top_k)

            # 3. 获取原文内容
            contexts = []
            for r in retrieved:
                doc_id = r.get("doc_id")
                if not doc_id:
                    continue
                # 从文档存储获取原文
                doc = self.doc_store.get_document(doc_id)
                if doc and doc.get("content"):
                    # 简单截断避免 prompt 过长
                    content = doc["content"][:self.context_truncate_length]
                    contexts.append(content)

            # 4. 生成
            answer = self.generate(query, contexts)

            # 5. 结果封装
            cost_time = time.time() - start_time
            return {
                "code": "SUCCESS",
                "message": "RAG 执行成功",
                "data": {
                    "query": query,
                    "top_k": top_k,
                    "contexts_count": len(contexts),
                    "answer": answer,
                    "retrieved": retrieved
                },
                "cost_time": cost_time
            }

        except RAGException:
            raise
        except Exception as e:
            self.logger.error(f"RAG 执行失败：{str(e)}")
            raise RAGException("RAG_RUN_FAILED", str(e))

    def call_rag(self, request: RAGRequest) -> RAGResponse:
        """实现抽象方法：标准化 RAG 调用入口，请求校验 + 异常封装"""
        try:
            # 1. 调用全流程
            result_dict = self.run(query=request.query, top_k=request.top_k)

            # 2. 转换为 RAGResponse 模型
            response = RAGResponse(
                code=result_dict["code"],
                message=result_dict["message"],
                data=result_dict.get("data"),
                cost_time=result_dict.get("cost_time"),
                trace_id=self.utils.get_assist_tool().get_current_time(format_="YYYYMMDDHHmmss")  # 简易 trace_id
            )
            return response

        except RAGException as e:
            return RAGResponse(
                code=e.code,
                message=e.message,
                data=None,
                trace_id=self.utils.get_assist_tool().get_current_time(format_="YYYYMMDDHHmmss")
            )
        except Exception as e:
            return RAGResponse(
                code="RAG_RUN_FAILED",
                message=str(e),
                data=None,
                trace_id=self.utils.get_assist_tool().get_current_time(format_="YYYYMMDDHHmmss")
            )