# -*- coding: utf-8 -*-
"""
查询改写(Query Rewrite)— 文档第 12.2 节

触发条件(由 SimpleRAG 判定):
    - 用户 query 太短(< 6 字)或过长(> 256 字)
    - 用户包含代词/省略(它/这个/上面那个)
    - 多轮对话需融合历史(若接入会话记忆)

输出契约(文档 12.2.2 节强制):
    {"rewrite_query": str, "keywords": List[str], "filters": Dict}
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class RewriteResult:
    """改写结果(文档 12.2.2 节)。

    rewrite_query 必须可直接送入 embedding。
    keywords 可选,辅助上层做关键词检索/高亮。
    filters 用于向量库 filter(如 doc_id/source 过滤)。
    """

    rewrite_query: str
    keywords: List[str] = field(default_factory=list)
    filters: Dict[str, Any] = field(default_factory=dict)

    def is_effective(self, original_query: str) -> bool:
        """改写后是否有实际变化(用于上游决定是否复用原 query)。"""
        return bool(self.rewrite_query) and self.rewrite_query.strip() != original_query.strip()


class BaseQueryRewriter(ABC):
    """查询改写抽象基类。"""

    @abstractmethod
    def should_rewrite(self, query: str) -> bool:
        """判断是否需要改写(默认实现见 _default_should_rewrite)。"""

    @abstractmethod
    def rewrite(self, query: str, context: Optional[Dict[str, Any]] = None) -> RewriteResult:
        """执行改写,返回 RewriteResult。

        参数:
            query: 用户原始查询
            context: 可选上下文(历史对话、当前 session 等)

        失败时应返回 RewriteResult(rewrite_query=query) 而非抛异常,
        让 RAG 主链路无缝降级。
        """


# ===========================================================================
# 默认实现: LLM-based 改写
# ===========================================================================


_REWRITE_PROMPT_TPL = """你是一个 RAG 查询改写助手。把用户的"原始查询"改写为更适合知识库检索的形式。

改写原则:
- 保留核心实体与名词(人名/产品名/版本号等)
- 把代词(它/这个/上面那个)替换为具体所指
- 太短的查询补充语境;太长的查询提取核心
- 不要改变查询意图

请只输出严格 JSON(不要解释/markdown 围栏),格式如下:
{{
  "rewrite_query": "改写后的查询",
  "keywords": ["可选关键词"],
  "filters": {{}}
}}

原始查询: {query}
"""


class LLMQueryRewriter(BaseQueryRewriter):
    """基于 LLM 的查询改写实现。"""

    DEFAULT_SHORT_THRESHOLD = 6
    DEFAULT_LONG_THRESHOLD = 256
    PRONOUN_PATTERN = re.compile(r"(它|这个|那个|上面那个|前面那个|前面提到的|上文)")

    def __init__(
        self,
        llm_call: Callable[[str], str],
        short_threshold: int = DEFAULT_SHORT_THRESHOLD,
        long_threshold: int = DEFAULT_LONG_THRESHOLD,
    ):
        """
        Args:
            llm_call: 接受 prompt 返回文本的可调用对象 (来自 LLMService.generate 等)
            short_threshold: query 长度 < 此值视为"太短",触发改写
            long_threshold: query 长度 > 此值视为"太长",触发改写
        """
        self.llm_call = llm_call
        self.short_threshold = short_threshold
        self.long_threshold = long_threshold

    def should_rewrite(self, query: str) -> bool:
        if not query:
            return False
        q = query.strip()
        if len(q) < self.short_threshold or len(q) > self.long_threshold:
            return True
        if self.PRONOUN_PATTERN.search(q):
            return True
        return False

    def rewrite(self, query: str, context: Optional[Dict[str, Any]] = None) -> RewriteResult:
        if not query or not self.llm_call:
            return RewriteResult(rewrite_query=query or "")

        prompt = _REWRITE_PROMPT_TPL.format(query=query)
        try:
            raw = self.llm_call(prompt)
        except Exception:
            # LLM 失败 -> 退化为原 query
            return RewriteResult(rewrite_query=query)

        parsed = _parse_rewrite_response(raw)
        if not parsed or not parsed.get("rewrite_query"):
            return RewriteResult(rewrite_query=query)

        keywords = parsed.get("keywords") or []
        if not isinstance(keywords, list):
            keywords = []
        filters = parsed.get("filters") or {}
        if not isinstance(filters, dict):
            filters = {}

        return RewriteResult(
            rewrite_query=str(parsed["rewrite_query"]),
            keywords=[str(k) for k in keywords],
            filters=filters,
        )


def _parse_rewrite_response(raw: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出抽 JSON,容忍 markdown 围栏。"""
    if not raw or not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\n?", "", candidate)
        candidate = re.sub(r"```\s*$", "", candidate).strip()
    match = re.search(r"\{[\s\S]*\}", candidate)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
