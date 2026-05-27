# -*- coding: utf-8 -*-
"""
重排(Rerank)— 文档第 12.4 节

输入:
    - query: 改写后的查询(rewrite_query)
    - candidates: vector_db 召回的 chunk 列表(retrieve_k 个,通常 50)

输出:
    - 同结构 chunk 列表,但按相关性重新排序,score 字段更新为 rerank 后的分数
    - 仅保留 top_k_rerank 个(默认 8)

设计:
    - BaseReranker: 抽象接口
    - LLMReranker: 默认实现,用 LLM 给每个候选打 0~1 相关性分,选 top_k
      (生产推荐替换为 cross-encoder 等专用模型,但当前用 LLM 演示完整链路)
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional


class BaseReranker(ABC):
    """重排抽象基类。"""

    @abstractmethod
    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_k: int = 8,
    ) -> List[Dict[str, Any]]:
        """执行重排。

        Args:
            query: 改写后的查询
            candidates: 检索召回的候选列表,每项至少含 chunk_id/content/score
            top_k: 重排后保留的数量上限

        返回:
            重排后的 candidates 子集(<= top_k),score 字段更新为 rerank 分数,
            按 score 降序排列。

        失败时应原样返回 candidates[:top_k] 而非抛异常,让 RAG 链路降级。
        """


# ===========================================================================
# 默认实现: LLM-based 重排
# ===========================================================================


_RERANK_PROMPT_TPL = """你是一个 RAG 检索结果重排助手。给定用户查询与候选文档片段,
对每个片段判断"是否与查询相关",给出 0~1 之间的相关性分数。

打分原则:
- 1.0: 直接回答了查询
- 0.7~0.9: 高度相关,提供了必要的上下文
- 0.4~0.6: 部分相关,涉及查询的某个子话题
- 0.0~0.3: 几乎不相关,关键词偶然匹配

请只输出严格 JSON 数组(不要解释/markdown 围栏),格式:
[
  {{"chunk_id": "<候选的 chunk_id>", "score": 0.85}},
  ...
]
要求:每个候选都打分,不能漏。

查询: {query}

候选片段:
{candidates}
"""


class LLMReranker(BaseReranker):
    """基于 LLM 的重排实现。"""

    DEFAULT_CONTENT_PREVIEW = 300

    def __init__(
        self,
        llm_call: Callable[[str], str],
        content_preview_chars: int = DEFAULT_CONTENT_PREVIEW,
    ):
        """
        Args:
            llm_call: 接受 prompt 返回文本的可调用对象
            content_preview_chars: 每个候选片段送入 prompt 的最大字符数
                (避免 prompt 过长,默认 300 字符够 LLM 判断相关性)
        """
        self.llm_call = llm_call
        self.content_preview_chars = content_preview_chars

    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_k: int = 8,
    ) -> List[Dict[str, Any]]:
        if not candidates:
            return []
        if not query or not self.llm_call:
            return candidates[:top_k]

        prompt = self._build_prompt(query, candidates)
        try:
            raw = self.llm_call(prompt)
        except Exception:
            # LLM 失败 -> 退化为原顺序截断
            return candidates[:top_k]

        score_map = _parse_rerank_response(raw)
        if not score_map:
            return candidates[:top_k]

        # 合并新分数到 candidates;LLM 漏打分的保留原 score
        reranked: List[Dict[str, Any]] = []
        for cand in candidates:
            cid = cand.get("chunk_id")
            new_cand = dict(cand)
            if cid in score_map:
                new_cand["score"] = float(score_map[cid])
                new_cand["rerank_source"] = "llm"
            reranked.append(new_cand)

        reranked.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return reranked[:top_k]

    def _build_prompt(self, query: str, candidates: List[Dict[str, Any]]) -> str:
        lines = []
        for i, c in enumerate(candidates, start=1):
            cid = c.get("chunk_id", f"unknown_{i}")
            content = (c.get("content") or "")[: self.content_preview_chars]
            lines.append(f"[{cid}] {content}")
        return _RERANK_PROMPT_TPL.format(query=query, candidates="\n\n".join(lines))


def _parse_rerank_response(raw: str) -> Optional[Dict[str, float]]:
    """从 LLM 输出抽 JSON 数组,提取 chunk_id -> score 映射。"""
    if not raw or not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\n?", "", candidate)
        candidate = re.sub(r"```\s*$", "", candidate).strip()
    match = re.search(r"\[[\s\S]*\]", candidate)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None

    score_map: Dict[str, float] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        cid = item.get("chunk_id")
        score = item.get("score")
        if cid and isinstance(score, (int, float)):
            try:
                score_map[str(cid)] = max(0.0, min(1.0, float(score)))
            except (TypeError, ValueError):
                continue
    return score_map or None
