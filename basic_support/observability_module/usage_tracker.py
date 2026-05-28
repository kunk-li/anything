# -*- coding: utf-8 -*-
"""
Token / Cost Tracker (Task Y #59)

集中追踪 LLM 调用的 token 用量 + USD 估算. 应用层 (ApiService / ConsoleApp)
或 LLMService.call_llm 完成后调 record(...) 喂数据; admin 面板从 snapshot() 拿汇总.

设计要点:
  - 模块级单例 (get_usage_tracker), 进程内全局
  - 线程安全 (threading.Lock)
  - per-model pricing 表 (USD per 1K tokens, prompt 与 completion 分开)
    缺失模型默认 0 (保守: 不估算价格, 让 token 数还能看)
  - snapshot() 返回:
        total: {prompt_tokens, completion_tokens, total_tokens, cost_usd}
        by_model: {model_name: {prompt, completion, total, cost_usd, calls}}
        by_tenant: 同形态, key 为 tenant_id (没 tenant 归 'default')
        recent: 最近 N 条 (默认 20) {timestamp, trace_id, tenant_id, model, prompt, completion, cost_usd}
  - reset() 给测试用

定价表 (2026 年 5 月报价, 单位 USD per 1K tokens)
  - 缺失模型 -> 0 USD (保守, 不估)
  - 运维通过 ANYTHING_LLM_PRICING (JSON) 或 config llm.pricing 覆盖
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Deque, Dict, Optional


# 默认 pricing 表 (USD per 1K tokens), 2026-05 公开 list price
# {model_name: {"prompt": price, "completion": price}}
_DEFAULT_PRICING: Dict[str, Dict[str, float]] = {
    # OpenAI 主流
    "gpt-4o": {"prompt": 0.005, "completion": 0.015},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4-turbo": {"prompt": 0.01, "completion": 0.03},
    "gpt-3.5-turbo": {"prompt": 0.0005, "completion": 0.0015},
    "text-embedding-3-small": {"prompt": 0.00002, "completion": 0.0},
    "text-embedding-3-large": {"prompt": 0.00013, "completion": 0.0},
    "text-embedding-ada-002": {"prompt": 0.0001, "completion": 0.0},
    # Anthropic
    "claude-3-5-sonnet": {"prompt": 0.003, "completion": 0.015},
    "claude-3-5-haiku": {"prompt": 0.0008, "completion": 0.004},
    "claude-3-opus": {"prompt": 0.015, "completion": 0.075},
    # 阿里 DashScope (qwen)
    "qwen-turbo": {"prompt": 0.0003 / 7.2, "completion": 0.0006 / 7.2},  # ≈ ¥0.0003 / ¥0.0006 兑 USD
    "qwen-plus": {"prompt": 0.0008 / 7.2, "completion": 0.002 / 7.2},
    "qwen-max": {"prompt": 0.02 / 7.2, "completion": 0.06 / 7.2},
    "qwen-vl-plus": {"prompt": 0.008 / 7.2, "completion": 0.008 / 7.2},
    # Ollama 本地 — 不计费
    "llama3": {"prompt": 0.0, "completion": 0.0},
    "mistral": {"prompt": 0.0, "completion": 0.0},
}


class UsageTracker:
    """全局 token / cost 累加器, 线程安全."""

    def __init__(self, max_recent: int = 50, pricing: Optional[Dict[str, Dict[str, float]]] = None):
        self._max_recent = max(1, int(max_recent))
        self._pricing = self._build_pricing(pricing)
        self._total = self._fresh_bucket()
        self._by_model: Dict[str, Dict[str, Any]] = {}
        self._by_tenant: Dict[str, Dict[str, Any]] = {}
        self._recent: Deque[Dict[str, Any]] = deque(maxlen=self._max_recent)
        self._lock = threading.Lock()

    @staticmethod
    def _fresh_bucket() -> Dict[str, Any]:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "calls": 0,
        }

    def _build_pricing(self, override: Optional[Dict[str, Dict[str, float]]]) -> Dict[str, Dict[str, float]]:
        merged: Dict[str, Dict[str, float]] = {k: dict(v) for k, v in _DEFAULT_PRICING.items()}
        # 环境变量覆盖: ANYTHING_LLM_PRICING='{"my-model":{"prompt":0.001,"completion":0.002}}'
        env_pricing = os.environ.get("ANYTHING_LLM_PRICING")
        if env_pricing:
            try:
                ext = json.loads(env_pricing)
                if isinstance(ext, dict):
                    for k, v in ext.items():
                        if isinstance(v, dict):
                            merged[k] = {
                                "prompt": float(v.get("prompt", 0.0)),
                                "completion": float(v.get("completion", 0.0)),
                            }
            except Exception:
                pass
        # 显式 override (构造时传入) 优先级最高
        if override:
            for k, v in override.items():
                if isinstance(v, dict):
                    merged[k] = {
                        "prompt": float(v.get("prompt", 0.0)),
                        "completion": float(v.get("completion", 0.0)),
                    }
        return merged

    def estimate_cost(
        self, model_name: Optional[str], prompt_tokens: int, completion_tokens: int
    ) -> float:
        """按 pricing 算 USD; 缺失模型 → 0 (不估)."""
        if not model_name:
            return 0.0
        p = self._pricing.get(model_name)
        if not p:
            return 0.0
        return (prompt_tokens / 1000.0) * p.get("prompt", 0.0) + (
            completion_tokens / 1000.0
        ) * p.get("completion", 0.0)

    def record(
        self,
        model_name: Optional[str],
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: Optional[float] = None,
        tenant_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """记一次 LLM 调用. 返回这条记录 dict (含估算后的 cost_usd)."""
        prompt_tokens = max(0, int(prompt_tokens or 0))
        completion_tokens = max(0, int(completion_tokens or 0))
        total = prompt_tokens + completion_tokens
        if cost_usd is None:
            cost_usd = self.estimate_cost(model_name, prompt_tokens, completion_tokens)
        cost_usd = float(cost_usd)
        record = {
            "timestamp": time.time(),
            "model": model_name or "(unknown)",
            "tenant_id": tenant_id or "default",
            "trace_id": trace_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total,
            "cost_usd": cost_usd,
        }
        with self._lock:
            self._accumulate(self._total, prompt_tokens, completion_tokens, cost_usd)
            self._accumulate(
                self._by_model.setdefault(record["model"], self._fresh_bucket()),
                prompt_tokens, completion_tokens, cost_usd,
            )
            self._accumulate(
                self._by_tenant.setdefault(record["tenant_id"], self._fresh_bucket()),
                prompt_tokens, completion_tokens, cost_usd,
            )
            self._recent.appendleft(record)
        return record

    @staticmethod
    def _accumulate(bucket: Dict[str, Any], p: int, c: int, usd: float) -> None:
        bucket["prompt_tokens"] += p
        bucket["completion_tokens"] += c
        bucket["total_tokens"] += p + c
        bucket["cost_usd"] += usd
        bucket["calls"] += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total": dict(self._total),
                "by_model": {k: dict(v) for k, v in sorted(self._by_model.items())},
                "by_tenant": {k: dict(v) for k, v in sorted(self._by_tenant.items())},
                "recent": list(self._recent)[:20],
                "pricing_known_models": sorted(self._pricing.keys()),
            }

    def reset(self) -> None:
        with self._lock:
            self._total = self._fresh_bucket()
            self._by_model.clear()
            self._by_tenant.clear()
            self._recent.clear()


# ============ 模块级单例 ============
_default: Optional[UsageTracker] = None
_default_lock = threading.Lock()


def get_usage_tracker() -> UsageTracker:
    global _default
    with _default_lock:
        if _default is None:
            _default = UsageTracker()
        return _default


def reset_usage_tracker() -> None:
    """主要给测试用."""
    global _default
    with _default_lock:
        _default = None
