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
from typing import Any, Deque, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    # Task XX (#84): 强类型 hint, runtime 不强依赖 state_backend_module
    from state_backend_module import StateBackend


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
    """全局 token / cost 累加器, 线程安全.

    Task XX (#84): 加 optional backend 参数让状态可放到 cross-process 后端:
        - backend=None (默认)         — 进程内 dict + Lock, 跟今天一致
        - backend=InMemoryBackend()   — 同上但走 StateBackend 接口 (测试用)
        - backend=SqliteBackend(path) — 多 worker 进程共享 token 计数 + 重启不丢
    """

    # Key 前缀, backend mode 时所有 usage 状态都打这个前缀, 方便 clear()
    _KEY_PREFIX = "usage:"

    def __init__(
        self,
        max_recent: int = 50,
        pricing: Optional[Dict[str, Dict[str, float]]] = None,
        backend: Optional["StateBackend"] = None,  # Task XX (#84)
    ):
        self._max_recent = max(1, int(max_recent))
        self._pricing = self._build_pricing(pricing)
        self._backend = backend
        # 进程内 state — 仅 backend=None 时使用
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
        # Task XX (#84): backend mode — 写穿到 cross-process backend
        if self._backend is not None:
            self._record_to_backend(record, prompt_tokens, completion_tokens, total, cost_usd)
            return record
        # legacy in-process mode
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

    def _record_to_backend(
        self, record: Dict[str, Any], p: int, c: int, total: int, usd: float
    ) -> None:
        """Task XX (#84): 把 record 增量写到 backend, 每个 counter 独立 incr 保证原子."""
        b = self._backend
        assert b is not None
        model = record["model"]
        tenant = record["tenant_id"]
        # 3 个 bucket × 5 counters = 15 个 incr 调用 — sqlite 已经在 BEGIN IMMEDIATE
        # 事务里, 跨进程争用时排队 (busy_timeout=5s), 不会撞 SQLITE_BUSY
        for prefix in (
            f"{self._KEY_PREFIX}total",
            f"{self._KEY_PREFIX}by_model:{model}",
            f"{self._KEY_PREFIX}by_tenant:{tenant}",
        ):
            b.incr(f"{prefix}:prompt_tokens", p)
            b.incr(f"{prefix}:completion_tokens", c)
            b.incr(f"{prefix}:total_tokens", total)
            b.incr(f"{prefix}:cost_usd", usd)
            b.incr(f"{prefix}:calls", 1)
        # 维护一个 known_models / known_tenants list, snapshot() 时 set() 去重
        # maxlen=200 防无界增长; 一般 <10 distinct, 重复 append 同一个名字是 OK 的
        b.list_append(f"{self._KEY_PREFIX}known_models", model, maxlen=200)
        b.list_append(f"{self._KEY_PREFIX}known_tenants", tenant, maxlen=200)
        # recent 列表 — backend list 已经有 maxlen FIFO 截
        b.list_append(f"{self._KEY_PREFIX}recent", record, maxlen=self._max_recent)

    @staticmethod
    def _accumulate(bucket: Dict[str, Any], p: int, c: int, usd: float) -> None:
        bucket["prompt_tokens"] += p
        bucket["completion_tokens"] += c
        bucket["total_tokens"] += p + c
        bucket["cost_usd"] += usd
        bucket["calls"] += 1

    def snapshot(self) -> Dict[str, Any]:
        # Task XX (#84): backend mode — 从 backend 重组各 bucket
        if self._backend is not None:
            return self._snapshot_from_backend()
        with self._lock:
            return {
                "total": dict(self._total),
                "by_model": {k: dict(v) for k, v in sorted(self._by_model.items())},
                "by_tenant": {k: dict(v) for k, v in sorted(self._by_tenant.items())},
                "recent": list(self._recent)[:20],
                "pricing_known_models": sorted(self._pricing.keys()),
            }

    def _snapshot_from_backend(self) -> Dict[str, Any]:
        """Task XX (#84): 从 backend 拼装 snapshot."""
        b = self._backend
        assert b is not None

        def _bucket(prefix: str) -> Dict[str, Any]:
            return {
                "prompt_tokens": int(b.get(f"{prefix}:prompt_tokens", 0) or 0),
                "completion_tokens": int(b.get(f"{prefix}:completion_tokens", 0) or 0),
                "total_tokens": int(b.get(f"{prefix}:total_tokens", 0) or 0),
                "cost_usd": float(b.get(f"{prefix}:cost_usd", 0) or 0),
                "calls": int(b.get(f"{prefix}:calls", 0) or 0),
            }

        models = sorted(set(b.list_get(f"{self._KEY_PREFIX}known_models")))
        tenants = sorted(set(b.list_get(f"{self._KEY_PREFIX}known_tenants")))
        recent = b.list_get(f"{self._KEY_PREFIX}recent", limit=20)
        # backend list 是 FIFO append, snapshot 期望最近的在前 (deque.appendleft 语义)
        recent = list(reversed(recent))

        return {
            "total": _bucket(f"{self._KEY_PREFIX}total"),
            "by_model": {m: _bucket(f"{self._KEY_PREFIX}by_model:{m}") for m in models},
            "by_tenant": {t: _bucket(f"{self._KEY_PREFIX}by_tenant:{t}") for t in tenants},
            "recent": recent,
            "pricing_known_models": sorted(self._pricing.keys()),
        }

    def reset(self) -> None:
        # Task XX (#84): backend mode — 走 backend.clear(prefix)
        if self._backend is not None:
            self._backend.clear(key_prefix=self._KEY_PREFIX)
            return
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
