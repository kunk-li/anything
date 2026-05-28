# -*- coding: utf-8 -*-
"""
BM25Retriever — 关键字稀疏检索 (Task #49)

实现 Okapi BM25 评分公式, 用于跟向量检索做混合 (hybrid retrieval).
向量检索靠语义, BM25 靠关键字精确命中; RRF (Reciprocal Rank Fusion)
把两路结果融合, 在 "查实体名 / 编号 / 缩写" 这类长尾 query 上提升明显。

设计要点:
  - 纯 Python 实现, 不依赖第三方 (rank_bm25 / elasticsearch 等),
    保证项目 zero-build 启动语义
  - tokenizer 走 "ASCII 单词 + 中文逐字" 双轨, 中文场景不需要分词器
    (jieba 等) 也能跑出像样结果
  - 持久化用 JSON, 体积稍大但跨平台/可读, 不依赖 pickle 的 ABI 锁定
  - 多租户当前用单文件存储 (run/bm25_index.json); 多租户隔离留给后续

BM25 公式 (Okapi):
    score(q, d) = Σ_t∈q  IDF(t) · (tf(t,d) · (k1+1)) / (tf(t,d) + k1·(1-b+b·|d|/avgdl))
    IDF(t)     = log((N - df(t) + 0.5) / (df(t) + 0.5) + 1)
  其中 k1=1.5, b=0.75 (业内默认值)
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from typing import Dict, List, Optional, Tuple


# ASCII 单词 ([a-zA-Z0-9_]+) 或单个中文/日韩字符
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[一-鿿぀-ヿ가-힯]")


def tokenize(text: str) -> List[str]:
    """轻量 tokenizer: ASCII 单词 + 中文逐字, 全部转小写.

    例: "RAG 检索 system 2.0" -> ["rag", "检", "索", "system", "2"]
        注意数字 '2.0' 会按 '_TOKEN_RE' 拆成 '2' (小数点不在字符类里),
        BM25 关键字检索场景里这种粒度通常可接受。
    """
    if not text:
        return []
    return [tok.lower() for tok in _TOKEN_RE.findall(text)]


class BM25Retriever:
    """Okapi BM25 关键字检索, 支持增量 add_chunks + save/load 持久化.

    线程安全: 多 worker 写共享单例时用一把锁保护 inverted_index.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        # 倒排索引: term -> {chunk_id: tf}
        self._inverted: Dict[str, Dict[str, int]] = {}
        # chunk_id -> chunk 元信息 (content, doc_id, file_name, chunk_index, ...)
        self._chunk_meta: Dict[str, Dict] = {}
        # chunk_id -> token 数 (用于 BM25 长度归一化)
        self._doc_lens: Dict[str, int] = {}
        # 累计 token 数, 用于增量算 avg_doc_len
        self._total_tokens: int = 0
        self._lock = threading.Lock()

    # ============ 索引 / 增量 ============

    def add_chunks(self, chunks: List[Dict]) -> int:
        """把一批 chunk 加入索引. chunk 至少需要 chunk_id + content.

        返回新加入的 chunk 数 (重复 chunk_id 会覆盖原条目).
        """
        if not chunks:
            return 0
        added = 0
        with self._lock:
            for c in chunks:
                if not isinstance(c, dict):
                    continue
                cid = c.get("chunk_id")
                content = c.get("content") or ""
                if not cid or not content:
                    continue
                # 同 chunk_id 重复: 先减掉旧贡献
                if cid in self._doc_lens:
                    self._total_tokens -= self._doc_lens[cid]
                    for term, postings in self._inverted.items():
                        postings.pop(cid, None)
                # 重新统计 token freq
                tokens = tokenize(content)
                if not tokens:
                    continue
                tf: Dict[str, int] = {}
                for t in tokens:
                    tf[t] = tf.get(t, 0) + 1
                for term, count in tf.items():
                    self._inverted.setdefault(term, {})[cid] = count
                self._doc_lens[cid] = len(tokens)
                self._total_tokens += len(tokens)
                # 复制 meta (避免外部修改污染索引)
                self._chunk_meta[cid] = {
                    "chunk_id": cid,
                    "doc_id": c.get("doc_id"),
                    "file_name": c.get("file_name"),
                    "chunk_index": c.get("chunk_index"),
                    "content": content,
                    "start_char": c.get("start_char"),
                    "end_char": c.get("end_char"),
                }
                added += 1
        return added

    def clear(self) -> None:
        """清空全部索引 (主要给 tests 用)."""
        with self._lock:
            self._inverted.clear()
            self._chunk_meta.clear()
            self._doc_lens.clear()
            self._total_tokens = 0

    @property
    def size(self) -> int:
        return len(self._doc_lens)

    @property
    def avg_doc_len(self) -> float:
        n = len(self._doc_lens)
        return (self._total_tokens / n) if n > 0 else 0.0

    # ============ 检索 ============

    def query(self, query_text: str, top_k: int = 10) -> List[Dict]:
        """BM25 检索, 返回 top_k 个 chunk (已含 score), 按分数降序.

        返回 chunk 结构跟 SimpleRAG.retrieve() 一致, 直接可塞进 chunks 流水线.
        """
        if not query_text or not self._doc_lens:
            return []
        q_tokens = list(set(tokenize(query_text)))  # 去重 (BM25 对重复 query term 不再加权)
        if not q_tokens:
            return []

        n = len(self._doc_lens)
        avgdl = self.avg_doc_len or 1.0

        # chunk_id -> 累积 score
        scores: Dict[str, float] = {}
        for term in q_tokens:
            postings = self._inverted.get(term)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(((n - df + 0.5) / (df + 0.5)) + 1.0)
            for cid, tf in postings.items():
                dl = self._doc_lens.get(cid, 0) or 1
                denom = tf + self.k1 * (1.0 - self.b + self.b * dl / avgdl)
                contrib = idf * (tf * (self.k1 + 1.0)) / denom
                scores[cid] = scores.get(cid, 0.0) + contrib

        if not scores:
            return []

        # top_k
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:max(1, top_k)]
        results = []
        for cid, score in ranked:
            meta = self._chunk_meta.get(cid)
            if not meta:
                continue
            out = dict(meta)
            out["score"] = float(score)
            results.append(out)
        return results

    # ============ 持久化 ============

    def save(self, path: str) -> bool:
        """把索引序列化到磁盘 (JSON). 失败返回 False."""
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            payload = {
                "k1": self.k1,
                "b": self.b,
                "inverted": self._inverted,
                "chunk_meta": self._chunk_meta,
                "doc_lens": self._doc_lens,
                "total_tokens": self._total_tokens,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            return True
        except Exception:
            return False

    def load(self, path: str) -> bool:
        """从磁盘恢复索引. 文件不存在/格式错误时返回 False, 状态保持空."""
        if not path or not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            with self._lock:
                self.k1 = float(payload.get("k1", self.k1))
                self.b = float(payload.get("b", self.b))
                self._inverted = {
                    term: {cid: int(tf) for cid, tf in postings.items()}
                    for term, postings in (payload.get("inverted") or {}).items()
                }
                self._chunk_meta = dict(payload.get("chunk_meta") or {})
                self._doc_lens = {
                    cid: int(n) for cid, n in (payload.get("doc_lens") or {}).items()
                }
                self._total_tokens = int(payload.get("total_tokens") or 0)
            return True
        except Exception:
            return False


# ============ Reciprocal Rank Fusion 融合 ============

def rrf_merge(
    rank_lists: List[List[Dict]],
    k: int = 60,
    id_field: str = "chunk_id",
) -> List[Dict]:
    """Reciprocal Rank Fusion: 把多路检索结果按 1/(k+rank) 累加分数后重排.

    参数:
        rank_lists: 多路检索的 chunk 列表 (已分别排序)
                    [[chunk, ...], [chunk, ...], ...]
        k:          RRF 常数 (默认 60, 业内常用), k 越大越 "民主",
                    每路对 top1 和 top10 的差异越平缓
        id_field:   chunk 内做唯一性的字段 (默认 chunk_id)

    返回: 融合后的 chunk 列表, 按 RRF 分数降序; chunk 取自第一次出现的那路.
          每个 chunk 多带一个 "rrf_score" 字段 (融合后的总分), 原 "score"
          保留为最高一路的得分以便上游观察.
    """
    if not rank_lists:
        return []
    fused: Dict[str, float] = {}
    representative: Dict[str, Dict] = {}
    max_per_id: Dict[str, float] = {}
    for lst in rank_lists:
        if not lst:
            continue
        for rank, item in enumerate(lst, start=1):
            if not isinstance(item, dict):
                continue
            uid = item.get(id_field)
            if not uid:
                continue
            fused[uid] = fused.get(uid, 0.0) + 1.0 / (k + rank)
            if uid not in representative:
                representative[uid] = dict(item)
            sc = item.get("score")
            if isinstance(sc, (int, float)):
                max_per_id[uid] = max(max_per_id.get(uid, float("-inf")), float(sc))
    if not fused:
        return []
    ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
    out = []
    for uid, fscore in ranked:
        rep = representative[uid]
        rep["rrf_score"] = float(fscore)
        if uid in max_per_id:
            rep["score"] = max_per_id[uid]
        out.append(rep)
    return out
