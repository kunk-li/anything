"""console_app 批处理模式测试 (Task T #54: 启用增强 UX 后回归)."""
from __future__ import annotations

import json
from pathlib import Path

from console_app_module.core.impl import ConsoleApp


class DummyHandler:
    def handle(self, request: dict) -> dict:
        return {
            "code": "SUCCESS",
            "message": "ok",
            "data": {"answer": request.get("query") or request.get("task")},
            "trace_id": "trace-batch",
            "retryable": False,
        }


def test_run_batch() -> None:
    app = ConsoleApp(handler=DummyHandler())
    results = app.run_batch([
        {"mode": "rag", "text": "hello"},
        {"mode": "agent", "text": "do something"},
    ])
    assert len(results) == 2
    assert all(item["code"] == "SUCCESS" for item in results)


def test_export_history(tmp_path: Path) -> None:
    app = ConsoleApp(handler=DummyHandler())
    app.run_batch([{"mode": "rag", "text": "hello"}])
    out = app.export_history(str(tmp_path / "history.json"), fmt="json")
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert len(data) == 1


def test_run_batch_file(tmp_path: Path) -> None:
    batch_file = tmp_path / "tasks.jsonl"
    batch_file.write_text('{"mode":"rag","text":"hello"}\n{"mode":"agent","text":"task"}\n', encoding="utf-8")
    app = ConsoleApp(handler=DummyHandler())
    results = app.run_batch_file(str(batch_file))
    assert len(results) == 2


def test_run_once_and_execute_share_consoleitem_history(tmp_path: Path) -> None:
    """回归: run_once 旧路径(预构造 {type} 请求)与 execute_request 新路径混存同一 store,
    现在两路统一产 ConsoleHistoryItem(此前旧路径存的是 plain dict, 与新路径记录类型不一)。
    保证记录类型一致, 任何依赖 .to_dict() 的读取方(export 等)对两路记录行为相同。"""
    app = ConsoleApp(handler=DummyHandler())
    app.run_once({"type": "rag", "query": "hello"})          # 旧路径
    app.run_batch([{"mode": "agent", "text": "world"}])      # 新路径 (execute_request)

    items = app.history_store.list_items()
    assert len(items) == 2
    # 关键: 两条都是 ConsoleHistoryItem(有 to_dict), 旧代码 run_once 那条会是 dict → 此断言会 fail
    assert all(hasattr(it, "to_dict") for it in items)
    out = app.export_history(str(tmp_path / "h.json"), fmt="json")
    assert len(json.loads(Path(out).read_text(encoding="utf-8"))) == 2
