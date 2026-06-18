# -*- coding: utf-8 -*-
"""
RagGenerationMixin (从 impl.py 拆出 — 上下文拼装与生成, 零行为变更)

    _assemble_context / _build_prompt / _call_llm_generate /
    _build_citations / _ensure_citation_marker

依赖 SimpleRAG (self): max_context_tokens, max_chunk_in_prompt_tokens, llm_client, logger
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from llm_compat import call_llm_compat
from common_utils_module import get_project_memory


class RagGenerationMixin:
    """上下文拼装 / prompt 构建 / LLM 生成 / citations。"""

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
