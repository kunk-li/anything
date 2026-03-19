from __future__ import annotations

from console_app_module.core.impl import ConsoleApp


class DummyHandler:
    def handle(self, request: dict) -> dict:
        return {"code": "SUCCESS", "message": "ok", "data": {"answer": "ok"}, "trace_id": "t", "retryable": False}


def test_mode_command_changes_state(capsys) -> None:
    app = ConsoleApp(handler=DummyHandler())
    app.handle_command(app.parse_input("/mode agent"))
    assert app.session.mode == "agent"


def test_topk_command_changes_state() -> None:
    app = ConsoleApp(handler=DummyHandler())
    app.handle_command(app.parse_input("/topk 9"))
    assert app.session.top_k == 9


def test_attach_and_clear() -> None:
    app = ConsoleApp(handler=DummyHandler())
    app.handle_command(app.parse_input("/attach ./a.pdf"))
    assert app.session.attachments == ["./a.pdf"]
    app.handle_command(app.parse_input("/clear_attach"))
    assert app.session.attachments == []
