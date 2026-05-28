# -*- coding: utf-8 -*-
"""
Quota / Rate limit (Task BB #62)

用 Z 的 hooks + Y 的 UsageTracker 实现:
  1. 日 USD 上限 — 当 tenant 今日累计 cost_usd 超过 daily_usd_limit 时, 拒新调用
  2. 每分钟请求次数 — sliding window counter, 超过 rate_per_minute 拒
  3. 全局 USD 上限 — 整个进程累计 cost (跨租户), 防止 LLM key 被刷爆

设计要点:
  - 限流 / 配额 都注册为 pre_llm_call hook + pre_tool_call hook (LLM 是主要成本源)
  - 失败时抛 BlockedError(code="QUOTA_EXCEEDED" 或 "RATE_LIMITED")
  - 配置通过 ConfigManager (config_module), 同时支持 env var override:
        ANYTHING_QUOTA_DAILY_USD     默认 None (不限)
        ANYTHING_QUOTA_GLOBAL_USD    默认 None (不限)
        ANYTHING_QUOTA_RATE_PER_MIN  默认 None (不限)
  - DEV_MODE 时整体跳过 (None 默认 → 不启用)

注册方式:
    from common_utils_module.utils.quota import install_quota_hooks
    install_quota_hooks(daily_usd_limit=10.0, rate_per_minute=30)
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, Optional

from hooks_module import BlockedError, get_hook_registry


class QuotaGuard:
    """单进程的配额 / 限流计数器. 线程安全.

    Sliding window rate limit: 用 deque 存最近 60s 的 timestamps,
    超出窗口的自动清理.
    Daily USD: 维护一个 (date_key, total_usd_today) tuple, 跨天重置.
    """

    def __init__(
        self,
        daily_usd_limit: Optional[float] = None,
        global_usd_limit: Optional[float] = None,
        rate_per_minute: Optional[int] = None,
    ):
        # None / 0 / 负数 都视为 "不启用"
        self.daily_usd_limit = self._coerce_positive(daily_usd_limit)
        self.global_usd_limit = self._coerce_positive(global_usd_limit)
        self.rate_per_minute = self._coerce_positive(rate_per_minute, kind=int)
        # per-tenant 今日 USD: {tenant_id: (date_key, usd)}
        self._daily_usd: Dict[str, Dict[str, float]] = {}
        # per-tenant 最近 60s 请求时间戳
        self._rate_window: Dict[str, Deque[float]] = {}
        # 全局 USD 累计 (跨重启会重置; 真正的全局 quota 应该用数据库)
        self._global_usd: float = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _coerce_positive(v, kind=float):
        if v is None:
            return None
        try:
            x = kind(v)
            return x if x > 0 else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _today_key() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _get_or_init_daily(self, tenant: str) -> Dict[str, float]:
        today = self._today_key()
        entry = self._daily_usd.get(tenant)
        if entry is None or entry.get("date") != today:
            entry = {"date": today, "usd": 0.0}
            self._daily_usd[tenant] = entry
        return entry

    def check_and_record(self, tenant_id: Optional[str], cost_usd: float = 0.0) -> None:
        """在 LLM/tool 调用前调一次, 自检 + 记录新请求.

        校验顺序: rate_per_minute → daily_usd → global_usd
        cost_usd 是这次预计要消耗的 USD; 0 表示先 record 时间戳, 实际 cost 在 post_llm
        阶段补记 record_cost. 现实场景里 LLM 调用前不知道精确 cost, 这里 cost_usd 主要
        给 "事后补登" 路径用.
        """
        tenant = tenant_id or "default"
        now = time.time()
        with self._lock:
            # 1. Rate limit
            if self.rate_per_minute is not None:
                window = self._rate_window.setdefault(tenant, deque())
                # 清理超出 60s 的旧时间戳
                threshold = now - 60.0
                while window and window[0] < threshold:
                    window.popleft()
                if len(window) >= self.rate_per_minute:
                    earliest = window[0]
                    retry_after = max(1, int(60 - (now - earliest)))
                    raise BlockedError(
                        f"tenant={tenant} 每分钟请求数超出限制 ({len(window)}/{self.rate_per_minute})",
                        code="RATE_LIMITED",
                        details={
                            "tenant_id": tenant,
                            "limit_per_minute": self.rate_per_minute,
                            "current_window": len(window),
                            "retry_after_seconds": retry_after,
                        },
                    )
                window.append(now)

            # 2. Daily USD
            if self.daily_usd_limit is not None and cost_usd > 0:
                entry = self._get_or_init_daily(tenant)
                if entry["usd"] + cost_usd > self.daily_usd_limit:
                    raise BlockedError(
                        f"tenant={tenant} 今日 USD 上限触顶 "
                        f"({entry['usd']:.4f}/{self.daily_usd_limit:.2f} USD)",
                        code="QUOTA_EXCEEDED",
                        details={
                            "tenant_id": tenant,
                            "daily_usd_limit": self.daily_usd_limit,
                            "daily_usd_used": entry["usd"],
                            "would_add": cost_usd,
                        },
                    )

            # 3. Global USD
            if self.global_usd_limit is not None and cost_usd > 0:
                if self._global_usd + cost_usd > self.global_usd_limit:
                    raise BlockedError(
                        f"全局 USD 上限触顶 "
                        f"({self._global_usd:.4f}/{self.global_usd_limit:.2f} USD)",
                        code="QUOTA_EXCEEDED",
                        details={
                            "global_usd_limit": self.global_usd_limit,
                            "global_usd_used": self._global_usd,
                            "would_add": cost_usd,
                        },
                    )

    def record_cost(self, tenant_id: Optional[str], cost_usd: float) -> None:
        """事后补登已发生的 cost (从 UsageTracker / LLM response). 不抛 BlockedError."""
        if cost_usd <= 0:
            return
        tenant = tenant_id or "default"
        with self._lock:
            entry = self._get_or_init_daily(tenant)
            entry["usd"] += cost_usd
            self._global_usd += cost_usd

    def snapshot(self) -> Dict[str, object]:
        """给 /admin/status 看用."""
        with self._lock:
            today = self._today_key()
            daily = {
                t: e.get("usd", 0.0)
                for t, e in self._daily_usd.items()
                if e.get("date") == today
            }
            return {
                "daily_usd_limit": self.daily_usd_limit,
                "global_usd_limit": self.global_usd_limit,
                "rate_per_minute": self.rate_per_minute,
                "today": today,
                "daily_usd_used_by_tenant": daily,
                "global_usd_used": round(self._global_usd, 6),
                "current_rate_window_size": {
                    t: len(w) for t, w in self._rate_window.items()
                },
            }

    def reset(self) -> None:
        """测试用. 清掉所有计数."""
        with self._lock:
            self._daily_usd.clear()
            self._rate_window.clear()
            self._global_usd = 0.0


# ============ 单例 + 配置加载 ============
_default: Optional[QuotaGuard] = None
_default_lock = threading.Lock()


def get_quota_guard() -> QuotaGuard:
    global _default
    with _default_lock:
        if _default is None:
            _default = QuotaGuard()
        return _default


def reset_quota_guard() -> None:
    global _default
    with _default_lock:
        _default = None


def configure_quota(
    daily_usd_limit: Optional[float] = None,
    global_usd_limit: Optional[float] = None,
    rate_per_minute: Optional[int] = None,
) -> QuotaGuard:
    """显式设置 quota. 触发新 singleton 实例 (旧的丢弃)."""
    global _default
    with _default_lock:
        _default = QuotaGuard(
            daily_usd_limit=daily_usd_limit,
            global_usd_limit=global_usd_limit,
            rate_per_minute=rate_per_minute,
        )
        return _default


def _load_quota_from_env() -> QuotaGuard:
    """从环境变量读 quota 配置. 都没设 → 一个空 QuotaGuard (不启用)."""
    daily = os.environ.get("ANYTHING_QUOTA_DAILY_USD")
    global_ = os.environ.get("ANYTHING_QUOTA_GLOBAL_USD")
    rate = os.environ.get("ANYTHING_QUOTA_RATE_PER_MIN")
    return QuotaGuard(
        daily_usd_limit=float(daily) if daily else None,
        global_usd_limit=float(global_) if global_ else None,
        rate_per_minute=int(rate) if rate else None,
    )


def install_quota_hooks(
    daily_usd_limit: Optional[float] = None,
    global_usd_limit: Optional[float] = None,
    rate_per_minute: Optional[int] = None,
) -> QuotaGuard:
    """一行安装: 注册 pre_llm_call + pre_tool_call hook + 设置单例.

    用法 (bootstrap 启动时):
        from common_utils_module.utils.quota import install_quota_hooks
        install_quota_hooks(daily_usd_limit=10.0, rate_per_minute=30)
    """
    if daily_usd_limit is None and global_usd_limit is None and rate_per_minute is None:
        # 都不设 → 从环境变量读
        guard = _load_quota_from_env()
        configure_quota(
            daily_usd_limit=guard.daily_usd_limit,
            global_usd_limit=guard.global_usd_limit,
            rate_per_minute=guard.rate_per_minute,
        )
    else:
        configure_quota(daily_usd_limit, global_usd_limit, rate_per_minute)
    guard = get_quota_guard()
    reg = get_hook_registry()

    def quota_pre_llm(prompt, model, ctx):
        # LLM 调用前: 主要看 rate limit; cost 还不知道, 先 0
        tenant = (ctx or {}).get("tenant_id")
        guard.check_and_record(tenant_id=tenant, cost_usd=0.0)

    def quota_pre_tool(tool_name, input_data, ctx):
        tenant = (ctx or {}).get("tenant_id")
        guard.check_and_record(tenant_id=tenant, cost_usd=0.0)

    def quota_post_llm(prompt, model, response, ctx):
        # LLM 调用后: 把这次 cost 登记进 daily / global
        # 用 UsageTracker.recent[0] 或 ctx 里的 cost_usd 都行
        tenant = (ctx or {}).get("tenant_id")
        cost = (ctx or {}).get("cost_usd")
        if cost is None:
            # 兜底: 从 usage_tracker 最近一条拿
            try:
                from observability_module import get_usage_tracker
                snap = get_usage_tracker().snapshot()
                recent = snap.get("recent") or []
                if recent:
                    cost = recent[0].get("cost_usd")
            except Exception:
                cost = None
        if cost:
            guard.record_cost(tenant_id=tenant, cost_usd=float(cost))

    reg.add_pre_llm_call(quota_pre_llm)
    reg.add_pre_tool_call(quota_pre_tool)
    reg.add_post_llm_call(quota_post_llm)
    return guard
