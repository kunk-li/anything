from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional, Protocol

from console_app_module.adapters.input_provider import BaseInputProvider, StdinInputProvider
from console_app_module.adapters.renderer import RendererFactory
from console_app_module.config.config import load_console_config
from console_app_module.core.base import BaseConsoleApp
from console_app_module.model.data_model import ConsoleHistoryItem, ConsoleInput, ConsoleRenderResult, ConsoleSessionConfig
from console_app_module.storage.history_store import BaseHistoryStore, InMemoryHistoryStore
from console_app_module.utils.tool_functions import build_request_dict, generate_session_id, iter_script_lines, load_batch_file, now_iso, parse_command


class RequestHandlerProtocol(Protocol):
    def handle(self, request: Dict[str, Any]) -> Dict[str, Any]:
        ...


class ConsoleApp(BaseConsoleApp):
    def __init__(
        self,
        handler: RequestHandlerProtocol,
        config: Optional[Dict[str, Any]] = None,
        history_store: Optional[BaseHistoryStore] = None,
        input_provider: Optional[BaseInputProvider] = None,
    ) -> None:
        self.handler = handler
        self.config = load_console_config(config)
        self.session = ConsoleSessionConfig(
            mode=self.config["default_mode"],
            session_id=generate_session_id(),
            top_k=self.config["default_top_k"],
            verbose=self.config["default_verbose"],
            renderer=self.config["default_renderer"],
        )
        self.history_store = history_store or InMemoryHistoryStore(max_size=self.config["history_max_size"])
        self.input_provider = input_provider or StdinInputProvider()
        self._running = False
        self._last_request: Optional[Dict[str, Any]] = None
        self._stats = {"total": 0, "success": 0, "failure": 0, "duration_ms": 0, "error_codes": {}}

    def run(self) -> None:
        self._running = True
        while self._running:
            raw = self.input_provider.read_line(self.session.prompt_text)
            if raw is None:
                print("收到 EOF，控制台安全退出。")
                break
            if not raw.strip():
                print("请输入内容，或使用 /help 查看帮助。")
                continue
            console_input = self.parse_input(raw)
            if console_input.is_command:
                self.handle_command(console_input)
                continue
            response = self.execute_request(console_input, source="interactive")
            self.print_render_result(self.render_response(response))

    def parse_input(self, text: str) -> ConsoleInput:
        return parse_command(text)

    def build_request(self, console_input: ConsoleInput) -> Dict[str, Any]:
        req = build_request_dict(console_input, self.session)
        self.session.session_id = req["session_id"]
        return req

    def render_response(self, response: Dict[str, Any]) -> ConsoleRenderResult:
        renderer = RendererFactory.create(self.session.renderer)
        return renderer.render(response, verbose=self.session.verbose)

    def print_render_result(self, result: ConsoleRenderResult) -> None:
        print(result.title)
        print(result.body)
        if result.footer:
            print(result.footer)

    def handle_command(self, console_input: ConsoleInput) -> bool:
        name = console_input.command_name or ""
        arg = console_input.command_arg or ""
        if name == "help":
            print(self.help_text())
            return True
        if name == "exit":
            self._running = False
            print("控制台已退出。")
            return True
        if name == "mode":
            if arg not in {"rag", "agent", "hybrid"}:
                print("mode 仅支持 rag / agent / hybrid")
                return True
            self.session.mode = arg
            print(f"当前模式已切换为: {arg}")
            return True
        if name == "topk":
            try:
                self.session.top_k = int(arg)
            except ValueError:
                print("top_k 必须是整数")
                return True
            print(f"当前 top_k = {self.session.top_k}")
            return True
        if name == "verbose":
            normalized = arg.lower()
            self.session.verbose = normalized in {"1", "on", "true", "yes"}
            print(f"verbose = {self.session.verbose}")
            return True
        if name == "session":
            if arg == "new" or not self.session.session_id:
                self.session.session_id = generate_session_id()
            print(f"session_id = {self.session.session_id}")
            return True
        if name == "history":
            limit = int(arg) if arg.isdigit() else 10
            items = self.history_store.list_items(self.session.session_id)[-limit:]
            print(self.format_history(items))
            return True
        if name == "export":
            path = arg or "./console_history.json"
            fmt = path.rsplit(".", 1)[-1] if "." in path else self.config["export_default_format"]
            exported = self.export_history(path, fmt=fmt)
            print(f"历史已导出到: {exported}")
            return True
        if name == "attach":
            if not arg:
                print("请提供附件路径")
                return True
            self.session.attachments.append(arg)
            print(f"已添加附件: {arg}")
            return True
        if name == "clear_attach":
            self.session.attachments.clear()
            print("附件列表已清空")
            return True
        if name == "multiline":
            self.session.multiline = arg.lower() in {"1", "on", "true", "yes"}
            print(f"multiline = {self.session.multiline}")
            return True
        if name == "batch":
            results = self.run_batch_file(arg)
            print(json.dumps({"count": len(results), "stats": self.stats_summary()}, ensure_ascii=False, indent=2))
            return True
        if name == "script":
            results = self.run_script_file(arg)
            print(json.dumps({"count": len(results), "stats": self.stats_summary()}, ensure_ascii=False, indent=2))
            return True
        if name == "retry":
            if not self._last_request:
                print("没有可重试的请求")
                return True
            response = self._invoke(self._last_request, source="retry")
            self.print_render_result(self.render_response(response))
            return True
        if name == "stats":
            print(json.dumps(self.stats_summary(), ensure_ascii=False, indent=2))
            return True
        if name == "theme":
            self.session.renderer = arg or "plain"
            print(f"renderer = {self.session.renderer}")
            return True
        print(f"未知命令: /{name}")
        return False

    def execute_request(self, console_input: ConsoleInput, source: str = "interactive") -> Dict[str, Any]:
        request = self.build_request(console_input)
        self._last_request = request
        return self._invoke(request, source=source)

    def _invoke(self, request: Dict[str, Any], source: str) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            response = self.handler.handle(request)
            if not isinstance(response, dict):
                response = {
                    "code": "INTERNAL_ERROR",
                    "message": "handler 返回结果不是 dict",
                    "data": {"raw": str(response)},
                    "trace_id": "",
                    "retryable": False,
                }
        except Exception as exc:  # noqa: BLE001
            response = {
                "code": "INTERNAL_ERROR",
                "message": str(exc),
                "data": None,
                "trace_id": "",
                "retryable": False,
                "details": {"exception_type": exc.__class__.__name__},
            }
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.setdefault("cost_time", round(duration_ms / 1000, 3))
        self._record_stats(response, duration_ms)
        item = ConsoleHistoryItem(
            timestamp=now_iso(),
            session_id=request.get("session_id") or self.session.session_id or "",
            request=request,
            response=response,
            duration_ms=duration_ms,
            source=source,
            tags=[request.get("type", "unknown")],
        )
        self.history_store.append(item)
        return response

    def _record_stats(self, response: Dict[str, Any], duration_ms: int) -> None:
        self._stats["total"] += 1
        self._stats["duration_ms"] += duration_ms
        code = response.get("code", "UNKNOWN")
        if code == "SUCCESS":
            self._stats["success"] += 1
        else:
            self._stats["failure"] += 1
            self._stats["error_codes"][code] = self._stats["error_codes"].get(code, 0) + 1

    def stats_summary(self) -> Dict[str, Any]:
        total = self._stats["total"]
        avg = (self._stats["duration_ms"] / total) if total else 0
        return {
            "total": total,
            "success": self._stats["success"],
            "failure": self._stats["failure"],
            "avg_duration_ms": round(avg, 2),
            "error_codes": dict(self._stats["error_codes"]),
        }

    def run_batch(self, items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for item in items:
            text = str(item.get("text") or item.get("query") or item.get("task") or "").strip()
            if not text:
                results.append({"code": "BAD_REQUEST", "message": "空任务", "data": item, "trace_id": ""})
                continue
            mode = item.get("type") or item.get("mode") or self.session.mode
            old_mode = self.session.mode
            self.session.mode = mode
            try:
                console_input = ConsoleInput(raw_text=text, cleaned_text=text)
                results.append(self.execute_request(console_input, source="batch"))
            finally:
                self.session.mode = old_mode
        return results

    def run_batch_file(self, path: str) -> List[Dict[str, Any]]:
        items = load_batch_file(path)
        return self.run_batch(items)

    def run_script_file(self, path: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for line in iter_script_lines(path):
            console_input = self.parse_input(line)
            if console_input.is_command:
                self.handle_command(console_input)
                continue
            results.append(self.execute_request(console_input, source="script"))
        return results

    def export_history(self, export_path: str, fmt: str = "json") -> str:
        return self.history_store.export(export_path, fmt=fmt)

    def format_history(self, items: List[ConsoleHistoryItem]) -> str:
        if not items:
            return "暂无历史记录"
        lines = []
        for idx, item in enumerate(items, start=1):
            lines.append(
                f"[{idx}] {item.timestamp} | {item.request.get('type')} | {item.response.get('code')} | {item.request.get('query') or item.request.get('task')}"
            )
        return "\n".join(lines)

    def help_text(self) -> str:
        return (
            "支持命令:\n"
            "/help 查看帮助\n"
            "/exit 退出\n"
            "/mode rag|agent|hybrid 切换模式\n"
            "/topk 8 修改检索数量\n"
            "/verbose on|off 切换详细输出\n"
            "/session new 查看或新建 session\n"
            "/history 10 查看最近历史\n"
            "/export ./out/history.json 导出历史\n"
            "/attach ./demo.pdf 添加附件路径\n"
            "/clear_attach 清空附件\n"
            "/batch ./examples/sample_batch.jsonl 批量执行\n"
            "/script ./examples/demo_commands.txt 脚本执行\n"
            "/retry last 重试最近一次请求\n"
            "/stats 查看统计\n"
            "/theme plain|rich 切换渲染主题"
        )
