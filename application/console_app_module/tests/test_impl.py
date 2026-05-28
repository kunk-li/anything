"""console_app 实现测试 (Task T #54: 启用增强 UX 后回归)."""
from __future__ import annotations

from console_app_module.core.impl import ConsoleApp
from console_app_module.model.data_model import ConsoleInput


class DummyHandler:
    def handle(self, request: dict) -> dict:
        text = request.get("query") or request.get("task")
        return {
            "code": "SUCCESS",
            "message": "ok",
            "data": {"answer": f"handled: {text}"},
            "trace_id": "trace-001",
            "retryable": False,
        }


def test_build_rag_request() -> None:
    app = ConsoleApp(handler=DummyHandler())
    req = app.build_request(ConsoleInput(raw_text="你好", cleaned_text="你好"))
    assert req["type"] == "rag"
    assert req["query"] == "你好"
    assert req["extra_params"]["source"] == "console_app"


def test_execute_request_records_history() -> None:
    app = ConsoleApp(handler=DummyHandler())
    response = app.execute_request(ConsoleInput(raw_text="你好", cleaned_text="你好"))
    assert response["code"] == "SUCCESS"
    assert len(app.history_store.list_items()) == 1


def test_render_response_contains_answer() -> None:
    app = ConsoleApp(handler=DummyHandler())
    result = app.render_response(
        {
            "code": "SUCCESS",
            "message": "ok",
            "data": {"answer": "abc"},
            "trace_id": "x",
            "cost_time": 0.1,
        }
    )
    assert "abc" in result.body
