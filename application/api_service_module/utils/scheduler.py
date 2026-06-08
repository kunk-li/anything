# -*- coding: utf-8 -*-
"""
任务调度器 (Task II #69)

后台线程定时触发 Agent / RAG 任务. 用 interval (秒数) 或简化 cron 表达式
(每天某点) 声明; 结果写入 audit log + 进程内 last_result.

设计目标:
  - 不引入新依赖 (不用 APScheduler / Celery)
  - 单进程内存计数器 + threading.Thread, 重启重置
  - 失败仅 WARN 不崩, 下次正常触发
  - 任务声明用 yaml 或 dict, 支持 dev_mode 跳过 (避免本地启停打扰)

简化 cron 仅支持:
    "every Xs"  / "every Xm"  / "every Xh"  → interval seconds
    "@daily 03:00"                            → 每天 UTC 03:00 触发

任务声明 dict:
    {
        "id": "morning_digest",
        "schedule": "@daily 09:00",
        "type": "rag",               # 走哪条业务链路: rag / agent / hybrid
        "body": {                    # 跟 /invoke 请求体一致
            "type": "rag",
            "query": "今天有什么新文档?",
            "top_k": 5,
            "tenant_id": "default",
        },
        "enabled": true,
    }
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional


def _parse_schedule(expr: str) -> Dict[str, Any]:
    """解析 schedule 字符串 → {'kind': 'interval'|'daily', 'seconds'?, 'hour'?, 'minute'?}.

    支持:
      "every Xs"   X 秒间隔
      "every Xm"   X 分钟
      "every Xh"   X 小时
      "@daily HH:MM"  每日 UTC HH:MM 触发
    解析失败抛 ValueError.
    """
    if not isinstance(expr, str):
        raise ValueError(f"schedule 必须是 str, 收到 {type(expr).__name__}")
    s = expr.strip().lower()

    m = re.fullmatch(r"every\s+(\d+)\s*(s|sec|secs|m|min|mins|h|hr|hrs)", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("s"):
            seconds = n
        elif unit.startswith("m"):
            seconds = n * 60
        else:  # h
            seconds = n * 3600
        if seconds < 1:
            raise ValueError(f"interval 必须 >= 1 秒, 收到 {seconds}")
        return {"kind": "interval", "seconds": seconds}

    m = re.fullmatch(r"@daily\s+(\d{1,2}):(\d{2})", s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"@daily 时间不合法: {s}")
        return {"kind": "daily", "hour": hour, "minute": minute}

    raise ValueError(
        f"无法解析 schedule: {expr!r}; 支持 'every Xs/m/h' 或 '@daily HH:MM'"
    )


def _next_fire_time(schedule: Dict[str, Any], now: Optional[datetime] = None) -> datetime:
    """根据 schedule + 当前时间算下一次触发的 UTC datetime."""
    now = now or datetime.now(timezone.utc)
    kind = schedule.get("kind")
    if kind == "interval":
        return now + timedelta(seconds=schedule["seconds"])
    if kind == "daily":
        candidate = now.replace(
            hour=schedule["hour"], minute=schedule["minute"], second=0, microsecond=0
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    raise ValueError(f"unknown schedule kind: {kind}")


def build_maintenance_task(
    schedule: str,
    scope: Optional[List[str]] = None,
    auto_apply: Optional[bool] = None,
    tenant_id: str = "default",
    task_id: str = "maintenance_scan",
) -> Dict[str, Any]:
    """方向1/4: 构造一条"自维护扫描"定时任务声明 (供 TaskScheduler.register)。

    body 走 agent 链路 + extra_params.maintenance_scan=true → 命中 SimpleAgent.execute 的
    maintenance_scan 钩子 → run_maintenance_scan(聚合 行为/记忆/代码文档 三域提议 + 通知)。
    scope 选域: 方向1 仅记忆域 ["memory"](reconcile/consolidate/prune); 全域省略 = 三域全扫。
    auto_apply: None=按 agent.auto_approve_maintenance 名单; 注意 reconcile/consolidate 永不自动
    (仅安全确定性算子 run_prune/run_degrade 可自动, 见 run_maintenance_scan 安全天花板)。
    注: 钩子要求 agent.enable_self_reflection=true, 否则 body 会被当普通 agent 任务执行
    (调用方 _build_scheduler 已在注册前 gate 此前提)。
    """
    return {
        "id": task_id,
        "schedule": schedule,
        "type": "agent",
        "body": {
            "type": "agent",
            "task": "scheduled maintenance scan",
            "tenant_id": tenant_id,
            "extra_params": {
                "maintenance_scan": True,
                "maintenance_scope": list(scope) if scope else ["behavior", "memory", "code_doc"],
                "auto_apply": auto_apply,
            },
        },
        "enabled": True,
    }


class TaskScheduler:
    """简单线程版任务调度器.

    用法:
        sch = TaskScheduler(handler=request_handler)
        sch.register(task_dict)
        sch.start()
        # ...
        sch.stop()

    handler 必须有 .handle(request, trace_id=...) 返回响应 envelope.
    """

    def __init__(
        self,
        handler: Any,
        logger: Any = None,
        audit_logger: Any = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self.handler = handler
        self.logger = logger
        self.audit_logger = audit_logger
        # 时钟可注入 (给测试用) — 默认 datetime.now(utc)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._tasks: Dict[str, Dict[str, Any]] = {}  # id -> task_def + state
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def register(self, task: Dict[str, Any]) -> None:
        """注册一条 task. 必填 id / schedule / body. 重复 id 报错."""
        if not isinstance(task, dict):
            raise ValueError("task 必须是 dict")
        tid = task.get("id")
        if not tid:
            raise ValueError("task.id 必填")
        schedule = task.get("schedule")
        if not schedule:
            raise ValueError(f"task {tid} 缺 schedule")
        body = task.get("body")
        if not isinstance(body, dict):
            raise ValueError(f"task {tid} body 必须是 dict")
        parsed = _parse_schedule(schedule)
        with self._lock:
            if tid in self._tasks:
                raise ValueError(f"task id 重复: {tid}")
            self._tasks[tid] = {
                "id": tid,
                "schedule": schedule,
                "schedule_parsed": parsed,
                "body": dict(body),
                "enabled": bool(task.get("enabled", True)),
                "next_run": _next_fire_time(parsed, self._clock()),
                "last_run": None,
                "last_result_code": None,
                "last_error": None,
                "runs": 0,
            }

    def unregister(self, task_id: str) -> bool:
        with self._lock:
            return self._tasks.pop(task_id, None) is not None

    # SchedulerRoutesMixin 的 DELETE /scheduler/{id} 调的是 cancel_task — 提供别名,
    # 让该路由在 scheduler 被接线后能正常工作 (此前 scheduler 恒为 None, 路由走不到此)。
    def cancel_task(self, task_id: str) -> bool:
        return self.unregister(task_id)

    def list_tasks(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": t["id"],
                    "schedule": t["schedule"],
                    "enabled": t["enabled"],
                    "next_run": t["next_run"].isoformat() if t["next_run"] else None,
                    "last_run": t["last_run"].isoformat() if t["last_run"] else None,
                    "last_result_code": t["last_result_code"],
                    "last_error": t["last_error"],
                    "runs": t["runs"],
                    "body_summary": {
                        "type": t["body"].get("type"),
                        "query": (t["body"].get("query") or t["body"].get("task") or "")[:80],
                    },
                }
                for t in self._tasks.values()
            ]

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="TaskScheduler", daemon=True)
        self._thread.start()
        if self.logger:
            self.logger.info(f"TaskScheduler 启动, 已注册 {len(self._tasks)} 个任务")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def trigger_once(self, task_id: str) -> Optional[Dict[str, Any]]:
        """手动触发一次. 给 admin / 测试用. 返回 handler response."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            task_snapshot = dict(task)
        return self._execute(task_snapshot)

    def _loop(self) -> None:
        """后台调度循环, 每秒醒一次扫所有任务, 到期的就触发."""
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                if self.logger:
                    self.logger.error(f"TaskScheduler tick 异常: {e}")
            # 1 秒粒度, stop_event 可立即唤醒
            self._stop_event.wait(timeout=1.0)

    def _tick(self) -> None:
        now = self._clock()
        due: List[Dict[str, Any]] = []
        with self._lock:
            for t in self._tasks.values():
                if not t["enabled"]:
                    continue
                if t["next_run"] and t["next_run"] <= now:
                    due.append(dict(t))
                    # 立刻 reschedule, 避免本 tick 重复触发
                    t["next_run"] = _next_fire_time(t["schedule_parsed"], now)
        for t in due:
            self._execute(t)

    def _execute(self, task_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        tid = task_snapshot["id"]
        body = dict(task_snapshot["body"])
        trace_id = f"sched-{tid}-{int(time.time())}"
        try:
            if self.logger:
                self.logger.info(f"[scheduler] 触发 task={tid} trace={trace_id}")
            resp = self.handler.handle(body, trace_id=trace_id)
            code = resp.get("code") if isinstance(resp, dict) else None
            self._record_run(tid, success=(code == "SUCCESS"), code=code, error=None)
            self._audit(tid, resp)
            return resp
        except Exception as e:
            err = str(e)
            if self.logger:
                self.logger.warning(f"[scheduler] task={tid} 执行异常: {err}")
            self._record_run(tid, success=False, code=None, error=err[:200])
            return {"code": "UNKNOWN_ERROR", "message": err, "trace_id": trace_id}

    def _record_run(self, tid: str, success: bool, code: Optional[str], error: Optional[str]) -> None:
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return
            t["runs"] += 1
            t["last_run"] = self._clock()
            t["last_result_code"] = code
            t["last_error"] = error if not success else None

    def _audit(self, tid: str, resp: Any) -> None:
        if self.audit_logger is None:
            return
        try:
            self.audit_logger.write({
                "timestamp_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": "scheduled_task_run",
                "task_id": tid,
                "code": (resp or {}).get("code") if isinstance(resp, dict) else None,
                "trace_id": (resp or {}).get("trace_id") if isinstance(resp, dict) else None,
            })
        except Exception:
            pass

    def snapshot(self) -> Dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "task_count": len(self._tasks),
            "tasks": self.list_tasks(),
        }
