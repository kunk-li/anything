"""console_app 命令模式测试 (Task T #54: /mode, /topk, /attach...)."""
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


# ============ Task X (#58) ============

def test_plan_command_toggles_session() -> None:
    app = ConsoleApp(handler=DummyHandler())
    assert app.session.plan_only is False
    app.handle_command(app.parse_input("/plan on"))
    assert app.session.plan_only is True
    app.handle_command(app.parse_input("/plan off"))
    assert app.session.plan_only is False
    app.handle_command(app.parse_input("/plan"))  # toggle 默认
    assert app.session.plan_only is True


def test_approve_command_appends_tools() -> None:
    app = ConsoleApp(handler=DummyHandler())
    app.handle_command(app.parse_input("/approve py_sandbox,http_request"))
    assert "py_sandbox" in app.session.approve_tools
    assert "http_request" in app.session.approve_tools
    # 重复 approve 不应重复添加
    app.handle_command(app.parse_input("/approve py_sandbox"))
    assert app.session.approve_tools.count("py_sandbox") == 1


def test_unapprove_clears_specific_tool() -> None:
    app = ConsoleApp(handler=DummyHandler())
    app.handle_command(app.parse_input("/approve py_sandbox,http_request"))
    app.handle_command(app.parse_input("/unapprove py_sandbox"))
    assert "py_sandbox" not in app.session.approve_tools
    assert "http_request" in app.session.approve_tools
    app.handle_command(app.parse_input("/unapprove"))  # 不带参 → 全清
    assert app.session.approve_tools == []


def test_plan_and_approve_propagate_to_request() -> None:
    """session 上设的 plan_only + approve_tools 应该通过 build_request 进 extra_params"""
    from console_app_module.model.data_model import ConsoleInput
    app = ConsoleApp(handler=DummyHandler())
    app.handle_command(app.parse_input("/plan on"))
    app.handle_command(app.parse_input("/approve py_sandbox"))
    req = app.build_request(ConsoleInput(raw_text="x", cleaned_text="x"))
    assert req["extra_params"]["plan_only"] is True
    assert req["extra_params"]["approve_tools"] == ["py_sandbox"]


def test_memory_command_no_crash_when_missing(capsys, tmp_path) -> None:
    """/memory 调用即使没找到文件也应该优雅打印, 不抛"""
    import os
    # 强制指向不存在的路径
    os.environ["ANYTHING_PROJECT_MEMORY"] = str(tmp_path / "nope.md")
    try:
        from common_utils_module import reset_project_memory
        reset_project_memory()
        app = ConsoleApp(handler=DummyHandler())
        app.handle_command(app.parse_input("/memory"))
        captured = capsys.readouterr()
        assert "ProjectMemory" in captured.out or "未找到" in captured.out
    finally:
        os.environ.pop("ANYTHING_PROJECT_MEMORY", None)
        from common_utils_module import reset_project_memory
        reset_project_memory()
