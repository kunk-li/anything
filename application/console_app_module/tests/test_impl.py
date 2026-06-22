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


def test_injected_input_provider_read_line_is_used() -> None:
    """注入的 ListInputProvider (read_line 契约) 必须被消费, 而不是被 input() 顶替.
    /exit 后主循环退出 — 若 provider 被忽略走 input() 会在 EOF 死循环/卡住."""
    from console_app_module.adapters.input_provider import ListInputProvider
    provider = ListInputProvider(["/topk 7", "/exit"])
    app = ConsoleApp(handler=DummyHandler(), input_provider=provider)
    app.run_interactive()  # 必须正常返回, 不挂起
    assert app.session.top_k == 7


def test_interactive_stops_on_input_exhaustion() -> None:
    """provider 输入耗尽返回 None = EOF 语义, 主循环应退出而非空转死循环."""
    from console_app_module.adapters.input_provider import ListInputProvider
    provider = ListInputProvider(["/mode agent"])  # 没有 /exit, 靠耗尽收尾
    app = ConsoleApp(handler=DummyHandler(), input_provider=provider)
    app.run_interactive()  # 不应挂起
    assert app.session.mode == "agent"


def test_injected_renderer_output_is_used() -> None:
    """注入的 BaseRenderer 子类 (render -> ConsoleRenderResult) 渲染结果应真正落成
    文本被打印, 而不是被 except-pass 吞掉返回 None."""
    from console_app_module.adapters.renderer import PlainRenderer
    app = ConsoleApp(handler=DummyHandler(), renderer=PlainRenderer())
    text = app._render_text(
        {"code": "SUCCESS", "message": "ok", "data": {"answer": "hello-render"}, "trace_id": "t"}
    )
    assert text  # 不再是 None / 空
    assert "hello-render" in text
