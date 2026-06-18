# -*- coding: utf-8 -*-
"""
SchedulerRoutesMixin (Task PPP #102)

3 个 cron 风格任务调度路由:
    GET    /scheduler/list           列已注册任务 + 状态
    POST   /scheduler/{task_id}/trigger  手动触发一次
    DELETE /scheduler/{task_id}      取消任务

self.scheduler 为 None 时所有路由返 SERVICE_UNAVAILABLE 501 (back-compat —
现有部署没启用 scheduler 也能正常跑).

VV (#82) 的 _register_v1_aliases 自动给 /scheduler/* 加 /v1/scheduler/* 镜像.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import Request
from ._envelope import envelope


class SchedulerRoutesMixin:
    """Cron 风格任务调度路由 mixin."""

    def _register_scheduler_routes(self) -> None:
        @self.app.get("/scheduler/list")
        async def scheduler_list(request: Request):
            trace_id = request.state.trace_id
            if self.scheduler is None:
                return envelope(trace_id, code="SERVICE_UNAVAILABLE", message="scheduler 未注入", status_code=501)
            try:
                tasks = self.scheduler.list_tasks()
                return envelope(trace_id, data={"count": len(tasks), "tasks": tasks})
            except Exception as e:
                return envelope(trace_id, code="SCHEDULER_LIST_FAILED", message=str(e), status_code=500)

        @self.app.post("/scheduler/{task_id}/trigger")
        async def scheduler_trigger(task_id: str, request: Request):
            trace_id = request.state.trace_id
            if self.scheduler is None:
                return envelope(trace_id, code="SERVICE_UNAVAILABLE", message="scheduler 未注入", status_code=501)
            try:
                result = self.scheduler.trigger_once(task_id)
                if result is None:
                    return envelope(trace_id, code="TASK_NOT_FOUND", message=f"task {task_id} 不存在", status_code=404)
                return envelope(trace_id, data={"task_id": task_id, "result": result})
            except Exception as e:
                return envelope(trace_id, code="SCHEDULER_TRIGGER_FAILED", message=str(e), status_code=500)

        @self.app.delete("/scheduler/{task_id}")
        async def scheduler_cancel(task_id: str, request: Request):
            trace_id = request.state.trace_id
            if self.scheduler is None:
                return envelope(trace_id, code="SERVICE_UNAVAILABLE", message="scheduler 未注入", status_code=501)
            try:
                ok = self.scheduler.cancel_task(task_id)
                if not ok:
                    return envelope(trace_id, code="TASK_NOT_FOUND", message=f"task {task_id} 不存在", status_code=404)
                return envelope(trace_id, data={"cancelled": True, "task_id": task_id})
            except Exception as e:
                return envelope(trace_id, code="SCHEDULER_CANCEL_FAILED", message=str(e), status_code=500)
