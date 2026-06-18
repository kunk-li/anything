# -*- coding: utf-8 -*-
"""
MetricsMixin (从 impl.py 拆出 — 指标/配额, 零行为变更)

    _is_known_tenant / _check_qps_quota / _bucket_tenant /
    _record_metrics / _render_prometheus_metrics

依赖 ApiService (self): __init__ 建的 metrics 计数器 / 锁 / quota 配置等实例字段。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


class MetricsMixin:
    """请求指标计数 + QPS 配额 + Prometheus 导出。"""

    def _is_known_tenant(self, tenant_id: str) -> bool:
        """PR4b: tenant_id 是否在已知集合中.

        未知 tenant_id (即便格式合法) -> ApiService 返回 404 TENANT_NOT_FOUND,
        防租户枚举攻击 (§9.3 统一不区分"存在但无权"与"不存在")。
        """
        return tenant_id in self._known_tenants

    def _check_qps_quota(self, tenant_id: str) -> bool:
        """PR4b QPS 滑动窗口检查.

        策略: per-tenant 1 秒窗口 deque[timestamp], 新请求来时:
            1. 弹掉窗外的旧 timestamp
            2. 若剩余数 >= max_qps 阈值, 拒绝并 ERROR 日志
            3. 否则 push 新 timestamp

        返回:
            True 表示放行, False 表示被限流 (调用方应返回 429 API_RATE_LIMITED)。
        """
        try:
            max_qps = self.config.get_config(f"quotas.{tenant_id}.max_qps", None)
        except Exception:
            max_qps = None
        if max_qps is None:
            return True
        try:
            max_qps_n = int(max_qps)
        except (TypeError, ValueError):
            return True
        if max_qps_n <= 0:
            return True

        from collections import deque as _deque
        now = time.time()
        with self._qps_lock:
            window = self._qps_windows.get(tenant_id)
            if window is None:
                window = _deque()
                self._qps_windows[tenant_id] = window
            # 弹出 1s 窗外的
            while window and (now - window[0]) > 1.0:
                window.popleft()
            if len(window) >= max_qps_n:
                self.logger.error(
                    f"[quota] QPS rate limit exceeded: tenant={tenant_id} "
                    f"current_window={len(window)} max_qps={max_qps_n}"
                )
                return False
            window.append(now)
        return True

    def _bucket_tenant(self, tenant_id: Optional[str]) -> str:
        """租户 cardinality 守护: allowlist 之外的全部聚合为 "other".

        见 docs/multi-tenancy-design.md §7.2 — 防 Prometheus 时间序列爆炸。
        """
        tid = (tenant_id or "default") or "default"
        if tid in self._tenant_label_allowlist:
            return tid
        return "other"

    def _record_metrics(
        self,
        req_type: str,
        code: str,
        duration: float,
        tenant_id: Optional[str] = None,
    ) -> None:
        """更新 metrics 计数. 持锁是因为 FastAPI 可能并发处理多请求.

        PR4a: 加入 tenant 标签维度, 用 (type, tenant) / (code, tenant) 元组作为 key,
        在渲染时拆开为 Prometheus 多标签。
        """
        with self._metrics_lock:
            req_type = req_type or "unknown"
            tenant = self._bucket_tenant(tenant_id)
            key_t = (req_type, tenant)
            self._metrics["requests_by_type"][key_t] = (
                self._metrics["requests_by_type"].get(key_t, 0) + 1
            )
            if code != "SUCCESS":
                key_e = (code, tenant)
                self._metrics["errors_by_code"][key_e] = (
                    self._metrics["errors_by_code"].get(key_e, 0) + 1
                )
            self._metrics["duration_sum_by_type"][key_t] = (
                self._metrics["duration_sum_by_type"].get(key_t, 0.0) + duration
            )
            self._metrics["duration_count_by_type"][key_t] = (
                self._metrics["duration_count_by_type"].get(key_t, 0) + 1
            )

    def _render_prometheus_metrics(self) -> str:
        """把 in-memory metrics 渲染为 Prometheus 文本格式.

        PR4a: 输出多标签 — type + tenant; errors_total 增加 tenant 标签。
        """
        with self._metrics_lock:
            snapshot = {
                k: dict(v) for k, v in self._metrics.items()
            }

        lines = []

        lines.append("# HELP anything_requests_total Total RAG/Agent requests handled")
        lines.append("# TYPE anything_requests_total counter")
        for (t, tenant), n in snapshot["requests_by_type"].items():
            lines.append(
                f'anything_requests_total{{type="{t}",tenant="{tenant}"}} {int(n)}'
            )

        lines.append("")
        lines.append("# HELP anything_errors_total Total non-SUCCESS responses by code")
        lines.append("# TYPE anything_errors_total counter")
        for (code, tenant), n in snapshot["errors_by_code"].items():
            lines.append(
                f'anything_errors_total{{code="{code}",tenant="{tenant}"}} {int(n)}'
            )

        lines.append("")
        lines.append("# HELP anything_request_duration_seconds_sum Cumulative request duration")
        lines.append("# TYPE anything_request_duration_seconds_sum counter")
        for (t, tenant), s in snapshot["duration_sum_by_type"].items():
            lines.append(
                f'anything_request_duration_seconds_sum{{type="{t}",tenant="{tenant}"}} {s:.6f}'
            )

        lines.append("")
        lines.append("# HELP anything_request_duration_seconds_count Total samples for duration")
        lines.append("# TYPE anything_request_duration_seconds_count counter")
        for (t, tenant), n in snapshot["duration_count_by_type"].items():
            lines.append(
                f'anything_request_duration_seconds_count{{type="{t}",tenant="{tenant}"}} {int(n)}'
            )

        return "\n".join(lines) + "\n"

    # =========================
    # 中间件与异常处理
    # =========================
