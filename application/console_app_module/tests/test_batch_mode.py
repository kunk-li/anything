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
