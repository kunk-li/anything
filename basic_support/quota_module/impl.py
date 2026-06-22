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
from typing import Deque, Dict, Optional, TYPE_CHECKING

from hooks_module import BlockedError, get_hook_registry

if TYPE_CHECKING:
    # Task AAA (#87): Phase 2 — cross-process backend, runtime 不强依赖
    from state_backend_module import StateBackend


class QuotaGuard:
    """配额 / 限流计数器, 线程安全.

    Task AAA (#87): 加 optional backend 参数, 让多 worker 进程共享限流计数.
    backend=None (默认) 进程内 dict + Lock (跟今天一致);
    backend=SqliteBackend(path) 让 4 个 worker 看同一份 rate window 和 daily USD,
    rate=30/min × 4 worker 不再变成实际 120/min.

    限流策略:
        Sliding window rate limit (in-process): deque 存最近 60s 时间戳
        Sliding window rate limit (backend):    list_append + list_get 过滤 > 60s 老的
        Daily USD: 跨天自动重置 (key 带 date 标记)
        Global USD: 进程内 / 跨进程累计 cost

    精度注意 (backend mode):
        check + record 不是原子 (StateBackend 无 compare-and-set), N workers
        竞争时可能溢出 ≤ N 个请求. 对生产场景一般可接受 — 真正强一致要用 Redis Lua.
    """

    # Task AAA (#87): backend mode 时的 key 前缀
    _KEY_PREFIX = "quota:"
    _DAILY_KEY = "quota:daily"
    _RATE_KEY = "quota:rate"
    _GLOBAL_KEY = "quota:global_usd"
    _KNOWN_TENANTS_KEY = "quota:known_tenants"

    def __init__(
        self,
        daily_usd_limit: Optional[float] = None,
        global_usd_limit: Optional[float] = None,
        rate_per_minute: Optional[int] = None,
        backend: Optional["StateBackend"] = None,  # Task AAA (#87)
    ):
        # None / 0 / 负数 都视为 "不启用"
        self.daily_usd_limit = self._coerce_positive(daily_usd_limit)
        self.global_usd_limit = self._coerce_positive(global_usd_limit)
        self.rate_per_minute = self._coerce_positive(rate_per_minute, kind=int)
        self._backend = backend  # Task AAA (#87): 跨进程后端
        # per-tenant 今日 USD: {tenant_id: (date_key, usd)} — 仅 backend=None 时用
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
        # Task AAA (#87): backend mode 分支
        if self._backend is not None:
            self._check_and_record_backend(tenant, now, cost_usd)
            return
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
            # 注意: pre_llm/pre_tool hook 总是传 cost_usd=0 (调用前不知道精确 cost),
            # 实际 cost 在 post_llm 阶段经 record_cost 累计. 因此这里必须用 "已累计总额"
            # 做闸门, 不能只在 cost_usd>0 时判 — 否则累计触顶后 cost_usd=0 的新请求会全部漏过,
            # daily 上限形同虚设. 闸门语义: 已累计 >= 上限 直接拒, 或本次 cost 会把累计推过上限.
            if self.daily_usd_limit is not None:
                entry = self._get_or_init_daily(tenant)
                if (
                    entry["usd"] >= self.daily_usd_limit
                    or entry["usd"] + cost_usd > self.daily_usd_limit
                ):
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

            # 3. Global USD (同上: 用已累计总额做闸门, 不依赖 cost_usd>0)
            if self.global_usd_limit is not None:
                if (
                    self._global_usd >= self.global_usd_limit
                    or self._global_usd + cost_usd > self.global_usd_limit
                ):
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
        # Task AAA (#87): backend mode 分支
        if self._backend is not None:
            today = self._today_key()
            self._backend.incr(f"{self._DAILY_KEY}:{tenant}:{today}:usd", cost_usd)
            self._backend.incr(self._GLOBAL_KEY, cost_usd)
            self._backend.list_append(self._KNOWN_TENANTS_KEY, tenant, maxlen=200)
            return
        with self._lock:
            entry = self._get_or_init_daily(tenant)
            entry["usd"] += cost_usd
            self._global_usd += cost_usd

    def snapshot(self) -> Dict[str, object]:
        """给 /admin/status 看用."""
        # Task AAA (#87): backend mode 分支
        if self._backend is not None:
            return self._snapshot_backend()
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
        # Task AAA (#87): backend mode 分支
        if self._backend is not None:
            self._backend.clear(key_prefix=self._KEY_PREFIX)
            return
        with self._lock:
            self._daily_usd.clear()
            self._rate_window.clear()
            self._global_usd = 0.0

    # ------------------------------------------------------------------
    # Task AAA (#87) backend mode 实现
    # ------------------------------------------------------------------

    def _check_and_record_backend(self, tenant: str, now: float, cost_usd: float) -> None:
        """走 StateBackend 跨进程共享, 4 个 worker 看同一份 quota state."""
        b = self._backend
        assert b is not None

        # 1. Rate limit (sliding window, 60s)
        if self.rate_per_minute is not None:
            rate_key = f"{self._RATE_KEY}:{tenant}"
            timestamps = b.list_get(rate_key)
            threshold = now - 60.0
            in_window = [float(t) for t in timestamps if float(t) > threshold]
            if len(in_window) >= self.rate_per_minute:
                earliest = min(in_window)
                retry_after = max(1, int(60 - (now - earliest)))
                raise BlockedError(
                    f"tenant={tenant} 每分钟请求数超出限制 ({len(in_window)}/{self.rate_per_minute})",
                    code="RATE_LIMITED",
                    details={
                        "tenant_id": tenant,
                        "limit_per_minute": self.rate_per_minute,
                        "current_window": len(in_window),
                        "retry_after_seconds": retry_after,
                    },
                )
            # 写新时间戳; maxlen 防止 list 无界增长 (2x rate 给宽容窗口)
            maxlen = max(self.rate_per_minute * 2, 100)
            b.list_append(rate_key, now, maxlen=maxlen)
            b.list_append(self._KNOWN_TENANTS_KEY, tenant, maxlen=200)

        # 2. Daily USD (跨进程共享, check + incr 非原子, 接受 ≤ N_workers 溢出)
        # 同 in-process 分支: pre hook cost_usd=0, 必须用已累计总额做闸门, 否则触顶后漏过.
        if self.daily_usd_limit is not None:
            today = self._today_key()
            daily_key = f"{self._DAILY_KEY}:{tenant}:{today}:usd"
            current = float(b.get(daily_key, 0) or 0)
            if (
                current >= self.daily_usd_limit
                or current + cost_usd > self.daily_usd_limit
            ):
                raise BlockedError(
                    f"tenant={tenant} 今日 USD 上限触顶 "
                    f"({current:.4f}/{self.daily_usd_limit:.2f} USD)",
                    code="QUOTA_EXCEEDED",
                    details={
                        "tenant_id": tenant,
                        "daily_usd_limit": self.daily_usd_limit,
                        "daily_usd_used": current,
                        "would_add": cost_usd,
                    },
                )

        # 3. Global USD (同上: 用已累计总额做闸门, 不依赖 cost_usd>0)
        if self.global_usd_limit is not None:
            current = float(b.get(self._GLOBAL_KEY, 0) or 0)
            if (
                current >= self.global_usd_limit
                or current + cost_usd > self.global_usd_limit
            ):
                raise BlockedError(
                    f"全局 USD 上限触顶 "
                    f"({current:.4f}/{self.global_usd_limit:.2f} USD)",
                    code="QUOTA_EXCEEDED",
                    details={
                        "global_usd_limit": self.global_usd_limit,
                        "global_usd_used": current,
                        "would_add": cost_usd,
                    },
                )

    def _snapshot_backend(self) -> Dict[str, object]:
        b = self._backend
        assert b is not None
        today = self._today_key()
        tenants = sorted(set(b.list_get(self._KNOWN_TENANTS_KEY)))
        daily = {
            t: float(b.get(f"{self._DAILY_KEY}:{t}:{today}:usd", 0) or 0)
            for t in tenants
        }
        # 用上 self.rate_per_minute 截掉 stale (>60s) 不算
        now = time.time()
        threshold = now - 60.0
        rate_sizes = {}
        for t in tenants:
            timestamps = b.list_get(f"{self._RATE_KEY}:{t}")
            in_window = [ts for ts in timestamps if float(ts) > threshold]
            rate_sizes[t] = len(in_window)
        return {
            "daily_usd_limit": self.daily_usd_limit,
            "global_usd_limit": self.global_usd_limit,
            "rate_per_minute": self.rate_per_minute,
            "today": today,
            "daily_usd_used_by_tenant": daily,
            "global_usd_used": round(float(b.get(self._GLOBAL_KEY, 0) or 0), 6),
            "current_rate_window_size": rate_sizes,
        }


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
    """从环境变量读 quota 配置. 都没设 → 一个空 QuotaGuard (不启用).

    env 值原样传给 QuotaGuard, 由 _coerce_positive 统一做转换 + 合法性判定:
    非数字 / 空 / <=0 一律视为 "不启用" (None), 不抛异常 — 跟模块 "invalid => disabled"
    契约一致. 之前直接 float()/int() 会在 env 配错 (如 DAILY_USD=abc) 时炸掉
    install_quota_hooks 整个启动流程.
    """
    return QuotaGuard(
        daily_usd_limit=os.environ.get("ANYTHING_QUOTA_DAILY_USD"),
        global_usd_limit=os.environ.get("ANYTHING_QUOTA_GLOBAL_USD"),
        rate_per_minute=os.environ.get("ANYTHING_QUOTA_RATE_PER_MIN"),
    )


def install_quota_hooks(
    daily_usd_limit: Optional[float] = None,
    global_usd_limit: Optional[float] = None,
    rate_per_minute: Optional[int] = None,
    registry=None,  # Task CCC (#89): 可注入 HookRegistry, 默认全局单例
) -> QuotaGuard:
    """一行安装: 注册 pre_llm_call + pre_tool_call hook + 设置 quota 单例.

    用法 (bootstrap 启动时):
        from quota_module import install_quota_hooks
        install_quota_hooks(daily_usd_limit=10.0, rate_per_minute=30)

    Task CCC (#89): 加 registry 参数, 让 bootstrap 可注入隔离的 HookRegistry
    (例: per-tenant registry / 测试 mock). 默认 None 时走全局单例 — back-compat.
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
    reg = registry if registry is not None else get_hook_registry()

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
