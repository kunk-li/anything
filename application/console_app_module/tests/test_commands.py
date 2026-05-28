"""console_app 命令模式测试 (/mode, /topk, /attach...). 见 test_batch_mode.py 跳过说明."""
from __future__ import annotations

import pytest

from console_app_module.core.impl import ConsoleApp

pytestmark = pytest.mark.skip(
    reason="ConsoleApp 命令模式 (session 状态机 + parse_input + handle_command) 未实现"
)


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
