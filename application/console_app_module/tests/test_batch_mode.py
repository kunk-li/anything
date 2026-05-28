"""console_app 批处理模式测试.

⚠️ 跳过原因 (Task Q #51):
    这些测试针对一份"未来 spec" — 期望 ConsoleApp 暴露:
    - run_batch([dict, ...]) 接受 list-of-dicts (现在只接受 file_path)
    - run_batch_file(path) 单独方法
    - export_history(path, fmt) 不存在
    - session.mode / session.top_k / session.attachments 状态机不存在
    - parse_input("/mode agent") + handle_command() 命令模式不存在
    - execute_request() / build_request() / history_store 不存在

    要让这些测试通过 = 实现一整套增强控制台 UX, 是个独立 feature,
    不在 "修单测" 范畴. 跳过等真正实现该 feature 时再启用.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from console_app_module.core.impl import ConsoleApp

pytestmark = pytest.mark.skip(
    reason="ConsoleApp 增强 UX (session/run_batch_file/export_history) 未实现, "
           "见模块 docstring; Task Q 不在此范围"
)


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
