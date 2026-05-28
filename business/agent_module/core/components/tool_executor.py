# -*- coding: utf-8 -*-
"""
ToolExecutorMixin (Task KK #71)

工具执行 + 缓存 + 审批门槛:
    _call_tool_with_retry       重试 + 缓存命中检查 + 失败包装
    _tool_cache_key/get/put     Task FF (#66) LRU 缓存 helpers
    tool_cache_stats            给 /admin/status 看用
    clear_tool_cache            主要给测试用
    _needs_approval             Task W (#57) 危险工具白名单门槛
    _get_tool                   兼容 ToolRegistry / dict 两种 registry
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional


class ToolExecutorMixin:
    """工具调用 + LRU 缓存 + 审批门槛."""

    # =========================
    # Task FF (#66): 工具结果缓存 LRU
    # =========================
    def _tool_cache_key(self, tool_name: str, payload: Dict[str, Any]) -> str:
        """生成 (tool_name + 排序后 input) 的稳定 hash key.

        刻意排除 trace_id / session_id / extra_params 等 'transient' 字段, 让
        同 query 不同 trace_id 也能命中缓存 (它们对工具输出不影响).
        """
        transient = {"trace_id", "session_id", "extra_params"}
        normalized = {k: v for k, v in payload.items() if k not in transient}
        try:
            key_str = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            key_str = repr(sorted(normalized.items()))
        h = hashlib.md5(f"{tool_name}|{key_str}".encode("utf-8")).hexdigest()
        return f"{tool_name}:{h}"

    def _tool_cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        """LRU 读: 命中后移到队尾 (最近用过). 未命中返回 None."""
        with self._tool_cache_lock:
            val = self._tool_cache.get(key)
            if val is None:
                self._tool_cache_stats["misses"] += 1
                return None
            self._tool_cache.move_to_end(key)
            self._tool_cache_stats["hits"] += 1
            return val

    def _tool_cache_put(self, key: str, value: Dict[str, Any]) -> None:
        """LRU 写: 超过 max_size 删除最旧."""
        if self.tool_cache_max_size <= 0:
            return
        with self._tool_cache_lock:
            self._tool_cache[key] = value
            self._tool_cache.move_to_end(key)
            while len(self._tool_cache) > self.tool_cache_max_size:
                self._tool_cache.popitem(last=False)

    def tool_cache_stats(self) -> Dict[str, Any]:
        """给 /admin/status 看用."""
        with self._tool_cache_lock:
            stats = dict(self._tool_cache_stats)
            total = stats["hits"] + stats["misses"]
            stats["hit_ratio"] = round(stats["hits"] / total, 3) if total else 0.0
            stats["size"] = len(self._tool_cache)
            stats["max_size"] = self.tool_cache_max_size
            stats["cacheable_tools"] = sorted(self.cacheable_tools)
            return stats

    def clear_tool_cache(self) -> None:
        """主要给测试用."""
        with self._tool_cache_lock:
            self._tool_cache.clear()
            self._tool_cache_stats = {"hits": 0, "misses": 0}

    # =========================
    # Task W (#57): 工具审批门槛
    # =========================
    def _needs_approval(self, tool_name: Optional[str], extra_params: Dict[str, Any]) -> bool:
        """判断该工具是否需要审批但未通过.

        返回 True = 在审批名单 且 extra_params.approve_tools 不含 → 中断.
        '*' 通配符代表批准所有危险工具.
        """
        if not tool_name:
            return False
        if tool_name not in self.tool_approval_required:
            return False
        approved = extra_params.get("approve_tools") or []
        if isinstance(approved, str):
            approved = [approved]
        if "*" in approved or tool_name in approved:
            return False
        return True

    # =========================
    # 工具调用 (含 cache 命中检查)
    # =========================
    def _call_tool_with_retry(
            self,
            step: Dict[str, Any],
            session_id: str,
            trace_id: Optional[str],
            max_retries: int,
    ) -> Dict[str, Any]:
        """按重试策略调用工具. Task FF (#66): 命中缓存直接返回, 减少重复触发."""
        step_id = step.get("step_id")
        tool_name = step.get("tool_name")
        payload = dict(step.get("input_data") or {})
        payload.setdefault("session_id", session_id)
        payload.setdefault("trace_id", trace_id)

        tool = self._get_tool(tool_name)
        if tool is None:
            return {
                "step_id": step_id,
                "tool_name": tool_name,
                "success": False,
                "output": None,
                "error": f"工具不存在：{tool_name}",
                "code": "TOOL_NOT_FOUND",
            }

        # Task FF: 缓存查询 — 仅对白名单工具有效
        cache_key = None
        if tool_name in self.cacheable_tools:
            cache_key = self._tool_cache_key(tool_name, payload)
            cached = self._tool_cache_get(cache_key)
            if cached is not None:
                cached_copy = dict(cached)
                cached_copy["step_id"] = step_id
                cached_copy["_cache_hit"] = True
                return cached_copy

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                output = tool(payload)

                is_success = True
                if isinstance(output, dict):
                    code = output.get("code")
                    if code and code != "SUCCESS":
                        is_success = False

                result = {
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "success": is_success,
                    "output": output,
                    "error": None if is_success else (
                        output.get("message", "tool returned non-success code")
                        if isinstance(output, dict) else "tool returned non-success result"
                    ),
                    "attempt": attempt + 1,
                }
                # 仅缓存成功的结果 (失败不缓存, 避免把暂时故障锁住)
                if cache_key and is_success:
                    self._tool_cache_put(cache_key, result)
                return result
            except Exception as e:
                last_error = str(e)
                self.logger.warning(
                    f"工具调用失败：tool={tool_name}, attempt={attempt + 1}, "
                    f"trace_id={trace_id}, error={last_error}"
                )

        return {
            "step_id": step_id,
            "tool_name": tool_name,
            "success": False,
            "output": None,
            "error": last_error or "tool call failed",
            "code": "TOOL_CALL_FAILED",
            "attempt": max_retries + 1,
        }

    def _get_tool(self, name: str):
        """从注册表获取工具，兼容 dict 和 ToolRegistry 两种风格."""
        if self.tool_registry is None:
            return None
        if hasattr(self.tool_registry, "get"):
            return self.tool_registry.get(name)
        if isinstance(self.tool_registry, dict):
            return self.tool_registry.get(name)
        return None
