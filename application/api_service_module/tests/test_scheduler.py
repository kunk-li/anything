# -*- coding: utf-8 -*-
"""
TaskScheduler 单元测试 (Task II #69)
"""

import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

from api_service_module.utils.scheduler import (
    TaskScheduler,
    _parse_schedule,
    _next_fire_time,
)


class _MockHandler:
    def __init__(self):
        self.calls = []
        self.responses_iter = None

    def handle(self, request, trace_id=None):
        self.calls.append({"request": dict(request), "trace_id": trace_id})
        if self.responses_iter is not None:
            try:
                return next(self.responses_iter)
            except StopIteration:
                pass
        return {"code": "SUCCESS", "message": "ok", "data": {"answer": "x"}, "trace_id": trace_id}


class _MockClock:
    """可控时钟 — 给测试用, 避免 sleep."""

    def __init__(self, start: datetime):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now = self.now + timedelta(seconds=seconds)


# ============ schedule 解析 ============

class TestScheduleParse(unittest.TestCase):

    def test_every_seconds(self):
        s = _parse_schedule("every 30s")
        self.assertEqual(s, {"kind": "interval", "seconds": 30})

    def test_every_minutes(self):
        s = _parse_schedule("every 5m")
        self.assertEqual(s, {"kind": "interval", "seconds": 300})

    def test_every_hours(self):
        s = _parse_schedule("every 2h")
        self.assertEqual(s, {"kind": "interval", "seconds": 7200})

    def test_every_variants(self):
        self.assertEqual(_parse_schedule("EVERY 10 mins")["seconds"], 600)
        self.assertEqual(_parse_schedule("every 3 hr")["seconds"], 10800)

    def test_daily(self):
        s = _parse_schedule("@daily 09:30")
        self.assertEqual(s, {"kind": "daily", "hour": 9, "minute": 30})

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            _parse_schedule("hello world")
        with self.assertRaises(ValueError):
            _parse_schedule("every 0s")
        with self.assertRaises(ValueError):
            _parse_schedule("@daily 25:00")


class TestNextFireTime(unittest.TestCase):

    def test_interval_adds(self):
        now = datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc)
        nxt = _next_fire_time({"kind": "interval", "seconds": 300}, now=now)
        self.assertEqual(nxt, now + timedelta(seconds=300))

    def test_daily_future_today(self):
        now = datetime(2026, 5, 28, 8, 0, 0, tzinfo=timezone.utc)
        nxt = _next_fire_time({"kind": "daily", "hour": 10, "minute": 0}, now=now)
        self.assertEqual(nxt, datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc))

    def test_daily_past_today_rolls_tomorrow(self):
        now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
        nxt = _next_fire_time({"kind": "daily", "hour": 9, "minute": 0}, now=now)
        self.assertEqual(nxt, datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc))


# ============ TaskScheduler ============

class TestTaskScheduler(unittest.TestCase):

    def setUp(self):
        self.clock = _MockClock(datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc))
        self.handler = _MockHandler()
        self.scheduler = TaskScheduler(handler=self.handler, clock=self.clock)

    def tearDown(self):
        self.scheduler.stop()

    def test_register_validates(self):
        with self.assertRaises(ValueError):
            self.scheduler.register("not a dict")
        with self.assertRaises(ValueError):
            self.scheduler.register({})  # 缺 id
        with self.assertRaises(ValueError):
            self.scheduler.register({"id": "x"})  # 缺 schedule
        with self.assertRaises(ValueError):
            self.scheduler.register({"id": "x", "schedule": "every 30s"})  # 缺 body
        with self.assertRaises(ValueError):
            self.scheduler.register({"id": "x", "schedule": "bad", "body": {}})

    def test_register_duplicate_id(self):
        self.scheduler.register({"id": "x", "schedule": "every 60s", "body": {"type": "rag"}})
        with self.assertRaises(ValueError):
            self.scheduler.register({"id": "x", "schedule": "every 30s", "body": {"type": "rag"}})

    def test_unregister(self):
        self.scheduler.register({"id": "x", "schedule": "every 60s", "body": {"type": "rag"}})
        self.assertTrue(self.scheduler.unregister("x"))
        self.assertFalse(self.scheduler.unregister("x"))

    def test_list_tasks(self):
        self.scheduler.register({"id": "a", "schedule": "every 60s", "body": {"type": "rag", "query": "abc"}})
        self.scheduler.register({"id": "b", "schedule": "@daily 09:00", "body": {"type": "agent", "task": "do"}})
        tasks = self.scheduler.list_tasks()
        self.assertEqual(len(tasks), 2)
        names = {t["id"] for t in tasks}
        self.assertEqual(names, {"a", "b"})

    def test_trigger_once_calls_handler(self):
        self.scheduler.register({"id": "x", "schedule": "every 60s",
                                  "body": {"type": "rag", "query": "hi"}})
        resp = self.scheduler.trigger_once("x")
        self.assertEqual(resp["code"], "SUCCESS")
        self.assertEqual(len(self.handler.calls), 1)
        self.assertEqual(self.handler.calls[0]["request"]["query"], "hi")

    def test_trigger_once_missing_task(self):
        self.assertIsNone(self.scheduler.trigger_once("doesnt_exist"))

    def test_tick_runs_due_task(self):
        self.scheduler.register({"id": "x", "schedule": "every 30s",
                                  "body": {"type": "rag", "query": "hi"}})
        # 推进 31s → 到期
        self.clock.advance(31)
        self.scheduler._tick()
        self.assertEqual(len(self.handler.calls), 1)
        # 没到下一周期, 再 tick 不会触发
        self.scheduler._tick()
        self.assertEqual(len(self.handler.calls), 1)
        # 又过 30s → 触发第二次
        self.clock.advance(30)
        self.scheduler._tick()
        self.assertEqual(len(self.handler.calls), 2)

    def test_disabled_task_not_run(self):
        self.scheduler.register({"id": "x", "schedule": "every 1s",
                                  "body": {"type": "rag"}, "enabled": False})
        self.clock.advance(10)
        self.scheduler._tick()
        self.assertEqual(len(self.handler.calls), 0)

    def test_handler_exception_does_not_kill_scheduler(self):
        """handler 抛异常时, 调度器记录失败但继续"""
        def boom_handler(request, trace_id=None):
            raise RuntimeError("intentional boom")
        self.scheduler.handler = type("H", (), {"handle": staticmethod(boom_handler)})()
        self.scheduler.register({"id": "x", "schedule": "every 30s",
                                  "body": {"type": "rag"}})
        self.clock.advance(31)
        self.scheduler._tick()  # 不应抛
        tasks = self.scheduler.list_tasks()
        self.assertEqual(tasks[0]["runs"], 1)
        self.assertIn("intentional boom", tasks[0]["last_error"])

    def test_audit_writes_record(self):
        class _AuditCapture:
            def __init__(self): self.records = []
            def write(self, rec): self.records.append(rec); return True
        audit = _AuditCapture()
        self.scheduler.audit_logger = audit
        self.scheduler.register({"id": "x", "schedule": "every 30s",
                                  "body": {"type": "rag"}})
        self.scheduler.trigger_once("x")
        self.assertEqual(len(audit.records), 1)
        self.assertEqual(audit.records[0]["event"], "scheduled_task_run")
        self.assertEqual(audit.records[0]["task_id"], "x")

    def test_snapshot(self):
        self.scheduler.register({"id": "x", "schedule": "every 30s", "body": {"type": "rag"}})
        snap = self.scheduler.snapshot()
        self.assertFalse(snap["running"])
        self.assertEqual(snap["task_count"], 1)
        self.assertEqual(snap["tasks"][0]["id"], "x")

    def test_start_stop_idempotent(self):
        self.scheduler.start()
        self.assertTrue(self.scheduler.snapshot()["running"])
        self.scheduler.start()  # 第二次启动应该 no-op
        self.scheduler.stop()
        self.assertFalse(self.scheduler.snapshot()["running"])
        self.scheduler.stop()  # 第二次停止也不应抛


# ============ 方向1/4: 自维护扫描任务声明 + cancel 别名 ============

class TestMaintenanceTaskAndAlias(unittest.TestCase):

    def test_build_maintenance_task_memory_scope(self):
        from api_service_module.utils.scheduler import build_maintenance_task
        t = build_maintenance_task("@daily 04:00", scope=["memory"], auto_apply=None)
        self.assertEqual(t["id"], "maintenance_scan")
        self.assertEqual(t["schedule"], "@daily 04:00")
        self.assertEqual(t["body"]["type"], "agent")
        ep = t["body"]["extra_params"]
        self.assertTrue(ep["maintenance_scan"])
        self.assertEqual(ep["maintenance_scope"], ["memory"])   # 方向1 仅记忆域
        self.assertIsNone(ep["auto_apply"])

    def test_build_maintenance_task_default_full_scope(self):
        from api_service_module.utils.scheduler import build_maintenance_task
        t = build_maintenance_task("every 6h")               # scope 省略 → 三域全扫 (方向4)
        self.assertEqual(t["body"]["extra_params"]["maintenance_scope"],
                         ["behavior", "memory", "code_doc"])
        # 声明可被 TaskScheduler 接受 (register 不抛)
        sch = TaskScheduler(handler=_MockHandler())
        sch.register(t)
        self.assertEqual(sch.list_tasks()[0]["id"], "maintenance_scan")

    def test_cancel_task_alias(self):
        # SchedulerRoutesMixin DELETE /scheduler/{id} 调 cancel_task — 须等价 unregister
        sch = TaskScheduler(handler=_MockHandler())
        sch.register({"id": "x", "schedule": "every 30s", "body": {"type": "rag"}})
        self.assertTrue(sch.cancel_task("x"))
        self.assertFalse(sch.cancel_task("x"))               # 已删, 再删返 False
        self.assertEqual(len(sch.list_tasks()), 0)


# ============ 方向1/4: 工厂接线 _build_scheduler ============

class _FakeConfig:
    """最小 config: get_config(dotted, default) + get_effective_value(...)."""
    def __init__(self, values):
        self._v = values

    def get_config(self, key, default=None):
        return self._v.get(key, default)

    def get_effective_value(self, key, env_var=None, default=None, value_type=None):
        return self._v.get(key, default)


class _FakeDeps:
    def __init__(self, config):
        self.config = config
        self.logger = None
        self.audit_logger = None


class TestBuildSchedulerWiring(unittest.TestCase):

    def _build(self, values):
        from factories.application_layer import _build_scheduler
        return _build_scheduler(_MockHandler(), _FakeDeps(_FakeConfig(values)))

    def test_no_config_returns_none(self):
        # 默认 (无 tasks / 无 maintenance_schedule) → 不接线, 不起线程
        self.assertIsNone(self._build({}))

    def test_maintenance_gated_off_when_self_reflection_off(self):
        # 配了 schedule 但 enable_self_reflection=false → 防呆跳过 (不会把扫描当普通任务跑)
        sch = self._build({"agent.maintenance_schedule": "@daily 04:00",
                           "agent.enable_self_reflection": False})
        self.assertIsNone(sch)

    def test_maintenance_registered_when_enabled(self):
        sch = self._build({"agent.maintenance_schedule": "@daily 04:00",
                           "agent.enable_self_reflection": True,
                           "agent.maintenance_scope": ["memory"]})
        self.assertIsNotNone(sch)
        try:
            tasks = sch.list_tasks()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["id"], "maintenance_scan")
        finally:
            sch.stop()

    def test_generic_tasks_registered_and_trigger_routes_to_handler(self):
        handler = _MockHandler()
        from factories.application_layer import _build_scheduler
        sch = _build_scheduler(handler, _FakeDeps(_FakeConfig({
            "scheduler": {"tasks": [
                {"id": "digest", "schedule": "every 1h",
                 "body": {"type": "rag", "query": "今天有什么新文档?"}},
            ]},
        })))
        self.assertIsNotNone(sch)
        try:
            res = sch.trigger_once("digest")          # 手动触发 → 走 handler.handle
            self.assertEqual(res["code"], "SUCCESS")
            self.assertEqual(handler.calls[0]["request"]["query"], "今天有什么新文档?")
        finally:
            sch.stop()


if __name__ == "__main__":
    unittest.main()
