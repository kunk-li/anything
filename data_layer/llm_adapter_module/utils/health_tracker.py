# -*- coding: utf-8 -*-
"""
LLM 模型健康追踪 (Task HH #68)

每次 LLM 调用的成功/失败被记入 ModelHealthTracker. 连续失败超阈值的模型
会被标 unhealthy, 冷却窗口内 LLMService 跳过它去试 fallback. 冷却结束自动
恢复 "probation" 状态, 下一次调用如果还失败立即重新冷却.

设计:
  - 单例 + 线程安全
  - 状态: healthy / unhealthy / probation
    - healthy:  正常使用
    - unhealthy: 冷却窗口内, fallback 跳过它
    - probation: 冷却结束后, 1 次重试; 成功 → healthy, 失败 → unhealthy again
  - 冷却时长 + 连续失败阈值 都可配置
  - snapshot() 给 /admin/status 看用

借鉴 Hystrix / Envoy 的 outlier detection.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional


class ModelHealthTracker:
    """模型健康状态记录器, 线程安全."""

    DEFAULT_FAIL_THRESHOLD = 3        # 连续失败 N 次 → unhealthy
    DEFAULT_COOLDOWN_SECONDS = 300    # 5 分钟冷却

    def __init__(
        self,
        fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    ):
        self._fail_threshold = max(1, int(fail_threshold))
        self._cooldown_seconds = max(1, int(cooldown_seconds))
        # {model_name: {state, consecutive_failures, last_fail_ts, total_calls, total_failures}}
        self._states: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _bucket(self, model_name: str) -> Dict[str, Any]:
        if model_name not in self._states:
            self._states[model_name] = {
                "state": "healthy",
                "consecutive_failures": 0,
                "last_fail_ts": 0.0,
                "total_calls": 0,
                "total_failures": 0,
            }
        return self._states[model_name]

    def is_available(self, model_name: str) -> bool:
        """检查模型是否可用. unhealthy 但已过冷却时间 → 进入 probation, 允许 1 次试探."""
        if not model_name:
            return False
        with self._lock:
            b = self._bucket(model_name)
            if b["state"] == "healthy":
                return True
            if b["state"] == "probation":
                return True  # 探针请求
            # unhealthy: 看冷却是否过了
            if (time.time() - b["last_fail_ts"]) >= self._cooldown_seconds:
                b["state"] = "probation"
                return True
            return False

    def record_success(self, model_name: str) -> None:
        if not model_name:
            return
        with self._lock:
            b = self._bucket(model_name)
            b["total_calls"] += 1
            b["consecutive_failures"] = 0
            # probation 成功 → healthy
            if b["state"] in ("probation", "unhealthy"):
                b["state"] = "healthy"

    def record_failure(self, model_name: str, error: str = "") -> None:
        if not model_name:
            return
        with self._lock:
            b = self._bucket(model_name)
            b["total_calls"] += 1
            b["total_failures"] += 1
            b["consecutive_failures"] += 1
            b["last_fail_ts"] = time.time()
            # 触发 unhealthy
            if b["state"] == "probation":
                # 探针失败 → 重新进入冷却
                b["state"] = "unhealthy"
            elif b["consecutive_failures"] >= self._fail_threshold:
                b["state"] = "unhealthy"
            # 记最后一次错误信息 (短截)
            b["last_error"] = (error or "")[:200]

    def snapshot(self) -> Dict[str, Any]:
        """给 /admin/status 看用."""
        with self._lock:
            now = time.time()
            models = {}
            for name, b in self._states.items():
                cd_remain = 0
                if b["state"] == "unhealthy":
                    cd_remain = max(0, int(self._cooldown_seconds - (now - b["last_fail_ts"])))
                models[name] = {
                    "state": b["state"],
                    "consecutive_failures": b["consecutive_failures"],
                    "total_calls": b["total_calls"],
                    "total_failures": b["total_failures"],
                    "failure_rate": (
                        round(b["total_failures"] / b["total_calls"], 3)
                        if b["total_calls"] else 0.0
                    ),
                    "cooldown_remaining_seconds": cd_remain,
                    "last_error": b.get("last_error", ""),
                }
            return {
                "fail_threshold": self._fail_threshold,
                "cooldown_seconds": self._cooldown_seconds,
                "models": models,
            }

    def reset(self) -> None:
        """测试用. 清掉所有状态."""
        with self._lock:
            self._states.clear()


# ============ 单例 ============
_default: Optional[ModelHealthTracker] = None
_default_lock = threading.Lock()


def get_health_tracker() -> ModelHealthTracker:
    global _default
    with _default_lock:
        if _default is None:
            _default = ModelHealthTracker()
        return _default


def reset_health_tracker() -> None:
    global _default
    with _default_lock:
        _default = None


def configure_health_tracker(
    fail_threshold: int = ModelHealthTracker.DEFAULT_FAIL_THRESHOLD,
    cooldown_seconds: int = ModelHealthTracker.DEFAULT_COOLDOWN_SECONDS,
) -> ModelHealthTracker:
    global _default
    with _default_lock:
        _default = ModelHealthTracker(
            fail_threshold=fail_threshold,
            cooldown_seconds=cooldown_seconds,
        )
        return _default
