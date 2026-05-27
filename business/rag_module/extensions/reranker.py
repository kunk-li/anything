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
    - LLMReranker: 用 LLM 给每个候选打 0~1 相关性分,选 top_k
      (功能完整, 但每次 rerank 都要 LLM 调用, 慢且贵)
    - CrossEncoderReranker: 用 sentence-transformers cross-encoder 模型本地推理
      (生产推荐, 比 LLM rerank 快 10x+, 无 LLM 调用费用, 质量稳定)
"""

from __future__ import annotations

import json
import math
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


# ===========================================================================
# 默认实现: Cross-encoder 本地推理
# ===========================================================================


class CrossEncoderReranker(BaseReranker):
    """基于 sentence-transformers CrossEncoder 的本地 rerank 实现。

    相比 LLMReranker:
        - 速度: ~10ms / batch(本地推理) vs LLM rerank 1-3 秒
        - 成本: 无 LLM 调用费用
        - 质量: 专为相关性打分训练, 比 prompt LLM 稳定
        - 缺点: 首次加载需下载模型 (~80MB for MiniLM)

    生产推荐用本类替代 LLMReranker。通过 config rag.reranker_type=
    "cross_encoder" 切换 (默认 "llm" 保持向后兼容)。
    """

    DEFAULT_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    DEFAULT_CONTENT_TRUNCATE = 512  # 单个 candidate 内容最大字符数, 避免超长 token

    def __init__(
        self,
        model_name: Optional[str] = None,
        model: Any = None,
        content_truncate_chars: int = DEFAULT_CONTENT_TRUNCATE,
    ):
        """
        Args:
            model_name: HuggingFace 模型名, 默认 cross-encoder/ms-marco-MiniLM-L-6-v2
                (轻量, 80MB, 中英文混合数据集训练)
            model: 显式注入 CrossEncoder 实例 (测试时可传 mock, 避免真实下载)
            content_truncate_chars: 单个候选 content 截断长度
        """
        self.model_name = model_name or self.DEFAULT_MODEL_NAME
        self._model = model  # 可被测试 mock 注入; 否则 lazy load
        self.content_truncate_chars = content_truncate_chars

    def _get_model(self) -> Any:
        """Lazy load CrossEncoder, 失败时返回 None 让 rerank 走降级路径"""
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(self.model_name)
        except Exception:
            self._model = None
        return self._model

    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_k: int = 8,
    ) -> List[Dict[str, Any]]:
        if not candidates:
            return []
        if not query:
            return candidates[:top_k]

        model = self._get_model()
        if model is None:
            # 模型加载失败 -> 退化为原顺序截断 (BaseReranker 契约要求)
            return candidates[:top_k]

        # 构造 (query, content) 对; predict 一次性 batch 推理
        try:
            pairs = [
                (query, (c.get("content") or "")[: self.content_truncate_chars])
                for c in candidates
            ]
            raw_scores = model.predict(pairs)
        except Exception:
            return candidates[:top_k]

        # CrossEncoder.predict 返回 logits (可能负数), 用 sigmoid 归一化到 [0,1]
        # 跟 LLMReranker 的 score 范围一致, 上游一视同仁
        try:
            normalized = [1.0 / (1.0 + math.exp(-float(s))) for s in raw_scores]
        except Exception:
            return candidates[:top_k]

        reranked: List[Dict[str, Any]] = []
        for cand, score in zip(candidates, normalized):
            new_cand = dict(cand)
            new_cand["score"] = float(score)
            new_cand["rerank_source"] = "cross_encoder"
            reranked.append(new_cand)

        reranked.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return reranked[:top_k]
