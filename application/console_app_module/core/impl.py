# -*- coding: utf-8 -*-
"""
控制台交互模块具体实现类
负责控制台输入、批处理、统一请求构造、调用接口层与终端输出渲染

Task T (#54) 增强:
    - session 状态机 (ConsoleSessionConfig): mode / top_k / attachments
    - parse_input: "/mode agent" 等命令 vs 普通 query
    - handle_command: /mode /topk /attach /clear_attach /help /history /exit
    - build_request: ConsoleInput 或 dict -> 统一 request body
    - execute_request: build → handler.handle → record history
    - history_store: 内存 list + export_history(path, fmt) JSON/JSONL 持久化
    - run_batch: 接受 list-of-dicts 或 文件路径 (向后兼容)
    - run_batch_file: 单独的 JSONL 文件入口
"""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from .base import BaseConsoleApp
from ..model.data_model import (
    ConsoleHistoryItem,
    ConsoleInput,
    ConsoleRenderResult,
    ConsoleSessionConfig,
)

from deps_module import BasicDeps, build_basic_deps, handle_exception_to_envelope


class _InMemoryHistoryStore:
    """内存版 history store, 默认实现. 测试 + 单进程 console app 用足够.

    生产场景可注入自定义 store (实现 save / list_items 方法即可).
    """

    def __init__(self) -> None:
        self._items: List[Union[ConsoleHistoryItem, Dict[str, Any]]] = []

    def save(self, item: Union[ConsoleHistoryItem, Dict[str, Any]]) -> bool:
        self._items.append(item)
        return True

    # 兼容老风格 append
    append = save

    def list_items(self) -> List[Union[ConsoleHistoryItem, Dict[str, Any]]]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()

    def export(self, path: str, fmt: str = "json") -> str:
        """写出到文件; 失败抛错. 返回最终落盘路径."""
        items = []
        for it in self._items:
            if hasattr(it, "to_dict"):
                items.append(it.to_dict())
            elif isinstance(it, dict):
                items.append(dict(it))
            else:
                items.append({"item": str(it)})
        p = Path(path)
        if p.parent and str(p.parent) not in ("", "."):
            p.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "jsonl":
            with p.open("w", encoding="utf-8") as f:
                for it in items:
                    f.write(json.dumps(it, ensure_ascii=False) + "\n")
        else:
            p.write_text(
                json.dumps(items, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return str(p)


class ConsoleApp(BaseConsoleApp):
    """标准控制台实现：run_once / run_interactive / run_batch"""

    def __init__(
        self,
        handler,
        input_provider=None,
        renderer=None,
        history_store=None,
        deps: Optional[BasicDeps] = None,
        session: Optional[ConsoleSessionConfig] = None,
    ):
        # 基础依赖优先走 DI 注入
        deps = deps or build_basic_deps()
        self.utils = deps.utils
        self.logger = deps.logger
        self.config = deps.config
        self.exception_handler = deps.exception_handler

        self.handler = handler
        self.input_provider = input_provider
        self.renderer = renderer
        # Task T: history_store 默认走内存版, 暴露 list_items() 给测试 + 增强 UX
        self.history_store = history_store if history_store is not None else _InMemoryHistoryStore()

        # Task T: session 状态机 (mode / top_k / attachments / session_id)
        self.session = session or ConsoleSessionConfig()

        self.default_render_mode = self.config.get_config(
            "console_app.default_render_mode", "friendly"
        )
        self.enable_history = bool(self.config.get_config("console_app.enable_history", True))
        self.batch_fail_fast = bool(self.config.get_config("console_app.batch_fail_fast", False))

        self.logger.info("控制台交互模块初始化完成")

    # =========================
    # Task T: 命令解析 / session 状态机
    # =========================
    # 已知命令一览, 加新命令直接往这里挂; key=命令名 (含同义词), value=方法名.
    _COMMAND_HANDLERS = {
        "mode":           "_cmd_mode",
        "m":              "_cmd_mode",
        "topk":           "_cmd_topk",
        "k":              "_cmd_topk",
        "attach":         "_cmd_attach",
        "clear_attach":   "_cmd_clear_attach",
        "clear":          "_cmd_clear_attach",
        "help":           "_cmd_help",
        "h":              "_cmd_help",
        "history":        "_cmd_history",
        "exit":           "_cmd_exit",
        "quit":           "_cmd_exit",
        "session_id":     "_cmd_session_id",
        "sid":            "_cmd_session_id",
        # Task X (#58): plan / approve / memory 借鉴 codex + claude code
        "plan":           "_cmd_plan",         # /plan on|off — 切 plan_only toggle
        "approve":        "_cmd_approve",      # /approve tool1,tool2 — 加白名单
        "unapprove":      "_cmd_unapprove",    # /unapprove tool1 — 撤白名单
        "memory":         "_cmd_memory",       # /memory — 显示当前项目记忆
    }

    def parse_input(self, text: str) -> ConsoleInput:
        """把原始输入解析成 ConsoleInput.
            "/mode agent"   -> is_command=True,  command_name='mode', command_arg='agent'
            "hello world"   -> is_command=False, cleaned_text='hello world'
        """
        raw = text if text is not None else ""
        cleaned = raw.strip()
        if cleaned.startswith("/"):
            body = cleaned[1:]
            parts = body.split(None, 1)
            cmd = parts[0].lower() if parts else ""
            arg = parts[1] if len(parts) > 1 else None
            return ConsoleInput(
                raw_text=raw,
                cleaned_text=cleaned,
                is_command=True,
                command_name=cmd,
                command_arg=arg,
            )
        return ConsoleInput(
            raw_text=raw,
            cleaned_text=cleaned,
            is_command=False,
        )

    def handle_command(self, console_input: ConsoleInput) -> bool:
        """处理 /xxx 命令. 返回 True 表示命令已被消费 (run_interactive 主循环不会再
        把它当 query 转发给 handler). 未知命令也返回 True (打印提示 + 不上 LLM).
        """
        if not console_input or not console_input.is_command:
            return False
        cmd = (console_input.command_name or "").lower()
        handler_name = self._COMMAND_HANDLERS.get(cmd)
        if handler_name is None:
            print(f"[unknown command] /{cmd}  (输入 /help 查看可用命令)")
            return True
        handler_fn = getattr(self, handler_name, None)
        if not callable(handler_fn):
            return True
        try:
            handler_fn(console_input.command_arg)
        except Exception as e:
            self.logger.warning(f"[console] /{cmd} 处理异常: {e}")
            print(f"[error] /{cmd}: {e}")
        return True

    # ---- 命令实现 ----
    def _cmd_mode(self, arg: Optional[str]) -> None:
        if not arg:
            print(f"current mode: {self.session.mode}")
            return
        m = arg.strip().lower()
        if m not in ("rag", "agent", "hybrid"):
            print(f"[error] mode 只支持 rag/agent/hybrid, 收到 {m!r}")
            return
        self.session.mode = m
        print(f"mode -> {m}")

    def _cmd_topk(self, arg: Optional[str]) -> None:
        if not arg:
            print(f"current top_k: {self.session.top_k}")
            return
        try:
            k = int(arg.strip())
        except (ValueError, TypeError):
            print(f"[error] top_k 必须是整数, 收到 {arg!r}")
            return
        # 上界与 schema_module.RequestEnvelope (top_k: ge=1, le=50) 对齐:
        # 控制台放行 >50 会让 build_request 出一个"看着合法"的请求, 却在接口层
        # schema 校验阶段被拒. 两处必须同界, 故此处固定为 50 (schema 是唯一真值源).
        if k < 1 or k > 50:
            print(f"[error] top_k 应在 [1,50], 收到 {k}")
            return
        self.session.top_k = k
        print(f"top_k -> {k}")

    def _cmd_attach(self, arg: Optional[str]) -> None:
        if not arg:
            print(f"current attachments: {self.session.attachments}")
            return
        self.session.attachments.append(arg.strip())
        print(f"attached: {arg.strip()}  (now {len(self.session.attachments)} files)")

    def _cmd_clear_attach(self, _arg: Optional[str]) -> None:
        n = len(self.session.attachments)
        self.session.attachments.clear()
        print(f"cleared {n} attachments")

    def _cmd_help(self, _arg: Optional[str]) -> None:
        print(self._help_text())

    def _cmd_history(self, _arg: Optional[str]) -> None:
        items = self.history_store.list_items() if self.history_store else []
        if not items:
            print("(暂无历史)")
            return
        for it in items[-20:]:
            d = it.to_dict() if hasattr(it, "to_dict") else dict(it)
            req = d.get("request", {})
            resp = d.get("response", {})
            print(f"  {d.get('timestamp', '?')}  type={req.get('type')}  "
                  f"code={resp.get('code')}  text={(req.get('query') or req.get('task') or '')[:40]}")

    def _cmd_exit(self, _arg: Optional[str]) -> None:
        # 命令本身只打印; 真正的退出由 run_interactive 主循环检测命令名后跳出
        print("(交互模式将在主循环中退出)")

    def _cmd_session_id(self, arg: Optional[str]) -> None:
        if not arg:
            print(f"current session_id: {self.session.session_id}")
            return
        self.session.session_id = arg.strip()
        print(f"session_id -> {self.session.session_id}")

    # ---- Task X (#58): plan / approve / memory ----
    def _cmd_plan(self, arg: Optional[str]) -> None:
        """/plan on|off|toggle. 不带参数则切换."""
        a = (arg or "").strip().lower()
        if a in ("on", "true", "1", "yes"):
            self.session.plan_only = True
        elif a in ("off", "false", "0", "no"):
            self.session.plan_only = False
        elif a in ("", "toggle"):
            self.session.plan_only = not self.session.plan_only
        else:
            print(f"[error] /plan 接受 on/off/toggle, 收到 {a!r}")
            return
        state = "ON (LLM 只输出计划, 待审批)" if self.session.plan_only else "OFF (直接执行)"
        print(f"plan mode -> {state}")

    def _cmd_approve(self, arg: Optional[str]) -> None:
        """/approve tool1,tool2 — 给工具发白名单. 不带参显示当前白名单."""
        if not arg:
            print(f"已审批工具: {self.session.approve_tools or '(无)'}")
            return
        for t in [x.strip() for x in arg.split(",") if x.strip()]:
            if t not in self.session.approve_tools:
                self.session.approve_tools.append(t)
        print(f"approve_tools -> {self.session.approve_tools}")

    def _cmd_unapprove(self, arg: Optional[str]) -> None:
        """/unapprove tool1,tool2 — 撤白名单. 不带参清空."""
        if not arg:
            self.session.approve_tools.clear()
            print("已清空 approve_tools")
            return
        for t in [x.strip() for x in arg.split(",") if x.strip()]:
            if t in self.session.approve_tools:
                self.session.approve_tools.remove(t)
        print(f"approve_tools -> {self.session.approve_tools}")

    def _cmd_memory(self, _arg: Optional[str]) -> None:
        """/memory — 显示当前生效的 ProjectMemory 文件 + 摘要."""
        try:
            from common_utils_module import get_project_memory
            mem = get_project_memory()
            path, n = mem.info()
            if path is None:
                print("(未找到 ProjectMemory 文件; 默认查 AGENTS.md / CLAUDE.md / .anything/memory.md)")
                return
            content = mem.load()
            preview = content[:400]
            print(f"ProjectMemory: {path}  ({n} chars)")
            print("--- preview (前 400 字) ---")
            print(preview)
            if n > 400:
                print(f"... [truncated, 总 {n} 字]")
        except Exception as e:
            print(f"[error] /memory: {e}")

    # =========================
    # Task T: 请求构造 / 执行 / 渲染
    # =========================
    def build_request(self, source: Union[ConsoleInput, Dict[str, Any], str]) -> Dict[str, Any]:
        """ConsoleInput / dict / str -> 统一 request body, 走 session 状态填默认值."""
        if isinstance(source, ConsoleInput):
            text = source.cleaned_text or source.raw_text or ""
            attachments = list(source.attachment_paths) if source.attachment_paths else list(self.session.attachments)
            mode = self.session.mode
        elif isinstance(source, dict):
            text = source.get("text") or source.get("query") or source.get("task") or ""
            attachments = list(source.get("attachments") or [])
            mode = source.get("mode") or source.get("type") or self.session.mode
        elif isinstance(source, str):
            text = source
            attachments = list(self.session.attachments)
            mode = self.session.mode
        else:
            raise TypeError(f"build_request 不支持的输入类型: {type(source).__name__}")

        # Task X (#58): plan_only + approve_tools 也透传 (Agent 端读 extra_params)
        extra: Dict[str, Any] = {
            "source": "console_app",
            "attachments": attachments,
        }
        if self.session.plan_only:
            extra["plan_only"] = True
        if self.session.approve_tools:
            extra["approve_tools"] = list(self.session.approve_tools)

        req: Dict[str, Any] = {
            "type": mode,
            "top_k": self.session.top_k,
            "extra_params": extra,
        }
        # rag 用 query, agent/hybrid 用 task; 同时也填一个 query 兜底, 不同 handler 都能拿到
        if mode == "rag":
            req["query"] = text
        else:
            req["task"] = text
            req["query"] = text
        if self.session.session_id:
            req["session_id"] = self.session.session_id
        return req

    def execute_request(self, console_input: Union[ConsoleInput, Dict[str, Any], str]) -> Dict[str, Any]:
        """一站式: build_request -> handler.handle -> 记 history. 返回 response."""
        request = self.build_request(console_input)
        trace_id = self._generate_trace_id()
        request.setdefault("trace_id", trace_id)
        start = time.time()
        try:
            response = self._call_handler(request, trace_id=trace_id)
        except Exception as e:
            self.logger.error(f"[console.execute] 异常: trace_id={trace_id}, err={e}")
            response = self._handle_exception(e, trace_id=trace_id, request=request)
        duration_ms = int((time.time() - start) * 1000)

        self._save_history_item(
            request=request, response=response,
            duration_ms=duration_ms, source="interactive",
        )
        return response

    def _call_handler(self, request: Dict[str, Any], trace_id: Optional[str] = None) -> Dict[str, Any]:
        """统一调 handler.handle. 优先用 (request, trace_id=...) 签名,
        若 handler 不接 kwargs (老式桩, 例如测试里的 DummyHandler), 回退到单参调用.
        """
        try:
            return self.handler.handle(request, trace_id=trace_id)
        except TypeError:
            return self.handler.handle(request)

    def export_history(self, export_path: str, fmt: str = "json") -> str:
        """把 history_store 内容写到文件, 返回最终路径. fmt: 'json' (默认, 数组) 或 'jsonl'."""
        if hasattr(self.history_store, "export"):
            return self.history_store.export(export_path, fmt=fmt)
        # store 没自带 export, 自己实现
        items = list(self.history_store.list_items()) if hasattr(self.history_store, "list_items") else []
        items_dicts = [it.to_dict() if hasattr(it, "to_dict") else dict(it) for it in items]
        p = Path(export_path)
        if p.parent and str(p.parent) not in ("", "."):
            p.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "jsonl":
            with p.open("w", encoding="utf-8") as f:
                for it in items_dicts:
                    f.write(json.dumps(it, ensure_ascii=False) + "\n")
        else:
            p.write_text(json.dumps(items_dicts, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(p)

    def run_batch_file(self, file_path: str) -> List[Dict[str, Any]]:
        """从 JSONL 文件加载 batch items 后跑 run_batch. 返回 response 列表."""
        items = self._load_jsonl_batch(Path(file_path))
        return self.run_batch(items)

    # =========================
    # 抽象接口实现
    # =========================
    def run(self) -> None:
        """ABC 契约入口 — 进入交互模式. 跟 run_interactive 等价."""
        self.run_interactive()

    def run_once(self, request: Dict[str, Any], trace_id: Optional[str] = None) -> Dict[str, Any]:
        """单次执行控制台请求"""
        start_time = time.time()
        trace_id = trace_id or request.get("trace_id") or self._generate_trace_id()

        try:
            standardized = self._standardize_console_request(request, trace_id=trace_id)

            self.logger.info(
                f"控制台单次执行开始：type={standardized.get('type')}, "
                f"trace_id={trace_id}, session_id={standardized.get('session_id')}"
            )

            result = self._call_handler(standardized, trace_id=trace_id)

            # Task T: render_response 现在返回 ConsoleRenderResult, 打印走 _render_text
            render_text = self._render_text(result)
            if render_text:
                print(render_text)

            self._save_history_item(
                request=standardized, response=result,
                duration_ms=int((time.time() - start_time) * 1000), source="batch",
            )

            self.logger.info(
                f"控制台单次执行完成：code={result.get('code')}, trace_id={trace_id}, "
                f"cost_time={round(time.time() - start_time, 3)}"
            )
            return result

        except Exception as e:
            self.logger.error(f"控制台单次执行异常：trace_id={trace_id}, error={str(e)}")
            error_response = self._handle_exception(e, trace_id=trace_id, request=request)
            try:
                print(self._render_text(error_response))
            except Exception:
                pass
            return error_response

    def run_interactive(self) -> None:
        """进入交互式控制台模式. Task T: 走 parse_input + handle_command/execute_request 新路径."""
        self.logger.info("进入控制台交互模式")
        print("进入交互模式。输入 /help 查看帮助, /exit 或 /quit 退出.")

        while True:
            try:
                raw = self._read_input(">>> ")
                # provider 返回 None = EOF / 输入耗尽 -> 收尾退出, 不能 continue
                # (注入 ListInputProvider/StdinInputProvider 时, continue 会空转死循环).
                if raw is None:
                    print("\n已退出控制台.")
                    break
                if not raw.strip():
                    continue

                ci = self.parse_input(raw)

                # 命令路径: /mode /topk /attach ... 由 handle_command 处理
                if ci.is_command:
                    cmd = (ci.command_name or "").lower()
                    if cmd in ("exit", "quit"):
                        print("已退出控制台.")
                        break
                    self.handle_command(ci)
                    continue

                # query 路径: execute_request 走完整流程 (build → handle → 写 history → print)
                response = self.execute_request(ci)
                try:
                    print(self._render_text(response))
                except Exception:
                    pass

            except (KeyboardInterrupt, EOFError):
                # 默认 input() 在 EOF (管道结束/Ctrl-D) 抛 EOFError; 若不显式捕获
                # 并 break, 主循环会被外层 except Exception 接住后无限重试 -> 死循环.
                print("\n已退出控制台.")
                break
            except Exception as e:
                self.logger.error(f"交互模式异常: {e}")
                print(f"[ERROR] 控制台输入处理失败: {e}")

    def run_batch(self, items: Union[str, Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """批量执行. items 接受两种 (向后兼容):
            - List[Dict[str, Any]]: 直接迭代, 每个 dict 形如 {mode, text} 或已组装的 request
            - str: 当作 JSONL 文件路径, 转调 run_batch_file
        返回 response 列表 (每条对应一个 item).
        Task T: 老版的 run_batch(file_path) 返回 dict 摘要不再支持 — 调用方改用
        run_batch_file 或直接看返回 list 的 success/fail.
        """
        if isinstance(items, str):
            return self.run_batch_file(items)
        items_list = list(items or [])
        if not items_list:
            return []

        results: List[Dict[str, Any]] = []
        for item in items_list:
            try:
                # 1) 已经是 request body (有 type 字段) 走 run_once 老路径
                if isinstance(item, dict) and "type" in item:
                    item_trace_id = item.get("trace_id") or self._generate_trace_id()
                    resp = self.run_once(item, trace_id=item_trace_id)
                else:
                    # 2) {mode, text, ...} 走 execute_request 新路径 (写 history)
                    resp = self.execute_request(item if isinstance(item, dict) else str(item))
                results.append(resp)
                if resp.get("code") != "SUCCESS" and self.batch_fail_fast:
                    break
            except Exception as e:
                self.logger.error(f"[console.batch] 异常 (容错继续): {e}")
                results.append({
                    "code": "UNKNOWN_ERROR",
                    "message": str(e),
                    "data": None,
                    "trace_id": self._generate_trace_id(),
                    "retryable": False,
                    "details": None,
                })
                if self.batch_fail_fast:
                    break
        return results

    # =========================
    # 对外辅助能力
    # =========================
    def render_response(self, response: Dict[str, Any], mode: Optional[str] = None) -> ConsoleRenderResult:
        """渲染统一响应为 ConsoleRenderResult (Task T: ABC 契约).

        body 字段优先放 answer / text / content; print() 出去时通过 _render_text
        走友好格式. 老调用方 `print(self.render_response(...))` 仍可工作 —
        ConsoleRenderResult 实现了 __str__ (走 _render_text 同款格式).
        """
        success = response.get("code") == "SUCCESS"
        data = response.get("data") or {}
        if isinstance(data, dict):
            body = (
                data.get("answer")
                or data.get("text")
                or data.get("content")
                or ""
            )
            if not body:
                body = json.dumps(data, ensure_ascii=False) if data else ""
        else:
            body = str(data) if data is not None else ""

        rendered_text = self._render_text(response, mode=mode)
        title = response.get("code", "UNKNOWN_ERROR")
        return ConsoleRenderResult(
            success=success,
            title=title,
            body=str(body) or rendered_text,
            footer=f"trace_id: {response.get('trace_id', '-')}",
            raw_response=response,
        )

    def _render_text(self, response: Dict[str, Any], mode: Optional[str] = None) -> str:
        """老的 str 渲染. 给 run_once 的 print() 用, 不再是 render_response 直接返回值."""
        mode = mode or self.default_render_mode

        if self.renderer is not None and hasattr(self.renderer, "render"):
            # 注入的 renderer 走 BaseRenderer 契约: render(response, verbose=bool)
            # -> ConsoleRenderResult. 这里负责把结构化结果落成可打印文本.
            # raw 模式仍走下面的内置 json 分支, 不经 renderer.
            if mode != "raw":
                try:
                    result = self.renderer.render(response, verbose=(mode != "brief"))
                    return self._render_result_to_text(result)
                except Exception as e:
                    # 不再吞掉 renderer 故障: 记 warning 让坏掉的 renderer 可见,
                    # 再 fall through 到内置文本渲染兜底.
                    self.logger.warning(f"[console] 注入 renderer 渲染失败, 回退内置渲染: {e}")

        code = response.get("code", "UNKNOWN_ERROR")
        trace_id = response.get("trace_id")
        message = response.get("message", "")
        data = response.get("data")
        details = response.get("details")

        if mode == "raw":
            return json.dumps(response, ensure_ascii=False, indent=2)

        if code == "SUCCESS":
            lines = ["[OK]"]
            if message:
                lines.append(f"message: {message}")

            if isinstance(data, dict):
                if "answer" in data:
                    lines.append(f"answer: {data.get('answer')}")
                elif "text" in data:
                    lines.append(f"text: {data.get('text')}")
                elif "content" in data:
                    lines.append(f"content: {data.get('content')}")
                else:
                    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
                if data.get("citations"):
                    lines.append(f"citations: {json.dumps(data.get('citations'), ensure_ascii=False)}")
            elif data is not None:
                lines.append(f"data: {data}")

            if trace_id:
                lines.append(f"trace_id: {trace_id}")
            return "\n".join(lines)

        lines = [f"[ERROR] {code}"]
        if message:
            lines.append(f"message: {message}")
        if trace_id:
            lines.append(f"trace_id: {trace_id}")
        if details is not None:
            try:
                lines.append(f"details: {json.dumps(details, ensure_ascii=False)}")
            except Exception:
                lines.append(f"details: {details}")
        return "\n".join(lines)

    @staticmethod
    def _render_result_to_text(result: "ConsoleRenderResult") -> str:
        """把 BaseRenderer 返回的 ConsoleRenderResult 落成可打印文本.

        ConsoleRenderResult 是纯 dataclass (无自定义 __str__), 故这里手动拼
        title / body / footer 三段. body 是主体, title/footer 为可选装饰.
        """
        parts: List[str] = []
        title = getattr(result, "title", None)
        body = getattr(result, "body", None)
        footer = getattr(result, "footer", None)
        if title:
            parts.append(str(title))
        if body:
            parts.append(str(body))
        if footer:
            parts.append(str(footer))
        return "\n".join(parts)

    # =========================
    # 内部辅助方法
    # =========================
    def _standardize_console_request(self, request: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
        """标准化控制台请求，确保符合系统统一请求格式"""
        standardized = dict(request)
        standardized["trace_id"] = trace_id

        if "extra_params" not in standardized or standardized["extra_params"] is None:
            standardized["extra_params"] = {}

        standardized["extra_params"].setdefault("source", "console")

        if "top_k" not in standardized or standardized.get("top_k") is None:
            standardized["top_k"] = 5

        return standardized

    def _read_input(self, prompt: str = "") -> Optional[str]:
        """读取输入，优先使用注入的 input_provider.

        provider 契约 (adapters.input_provider.BaseInputProvider) 暴露
        read_line(prompt) -> Optional[str], EOF/输入耗尽时返回 None. 故先探
        read_line, 再退回历史的 read / callable 形态, 最后才是内置 input().
        """
        if self.input_provider is not None:
            if hasattr(self.input_provider, "read_line"):
                return self.input_provider.read_line(prompt)
            if hasattr(self.input_provider, "read"):
                return self.input_provider.read(prompt)
            if callable(self.input_provider):
                return self.input_provider(prompt)
        return input(prompt)

    def _parse_interactive_input(self, raw: str) -> Dict[str, Any]:
        """解析交互式输入为统一请求格式"""
        # 支持：
        # rag: 什么是RAG？
        # agent: 请总结这段文本
        # hybrid: 基于知识库回答这个问题
        if ":" in raw:
            head, body = raw.split(":", 1)
            req_type = head.strip().lower()
            body = body.strip()

            if req_type == "rag":
                return {"type": "rag", "query": body, "top_k": 5}
            if req_type == "agent":
                return {"type": "agent", "task": body}
            if req_type == "hybrid":
                return {"type": "hybrid", "task": body}

        # 默认当作 rag query
        return {"type": "rag", "query": raw, "top_k": 5}

    def _load_jsonl_batch(self, path: Path) -> List[Dict[str, Any]]:
        """加载 JSONL 批处理文件"""
        items = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"JSONL 第 {line_no} 行不是合法 JSON: {str(e)}")
            if not isinstance(obj, dict):
                raise ValueError(f"JSONL 第 {line_no} 行必须是 JSON 对象")
            items.append(obj)
        return items

    def _load_text_batch(self, path: Path) -> List[Dict[str, Any]]:
        """加载文本命令批处理文件"""
        items = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            items.append(self._parse_interactive_input(line))
        return items

    def _save_history_item(self, *, request: Dict[str, Any], response: Dict[str, Any],
                           duration_ms: int, source: str) -> None:
        """统一记一条 ConsoleHistoryItem 到 history_store (两条执行路径共用)。

        修旧 bug: run_once 旧路径曾存 plain dict, 与 execute_request 存的 ConsoleHistoryItem
        混进同一个 store; 之后 export()/list_items(session_id) 调 .to_dict()/.session_id
        会在 dict 上 AttributeError 直接崩。现在两路统一产 ConsoleHistoryItem。
        失败不阻断主流程。history_store 在 __init__ 必被赋值 (默认内存版), 故无文件回落。
        """
        if not self.enable_history or self.history_store is None:
            return
        item = ConsoleHistoryItem(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            session_id=self.session.session_id or "default",
            request=request,
            response=response,
            duration_ms=duration_ms,
            source=source,
        )
        try:
            if hasattr(self.history_store, "save"):
                self.history_store.save(item)
            elif hasattr(self.history_store, "append"):
                self.history_store.append(item)
        except Exception as e:
            self.logger.warning(f"控制台历史保存失败(已忽略): {e}")

    def _help_text(self) -> str:
        plan_state = "ON" if self.session.plan_only else "OFF"
        approved = ",".join(self.session.approve_tools) or "(空)"
        return (
            "可用命令 (Task T + X 增强):\n"
            "  /mode <rag|agent|hybrid>   切换模式 (当前 = " + str(self.session.mode) + ")\n"
            "  /topk <N>                   设置 top_k (当前 = " + str(self.session.top_k) + ")\n"
            "  /attach <path>              添加附件路径\n"
            "  /clear_attach               清空附件\n"
            "  /session_id <id>            绑定 session_id (跨请求关联)\n"
            "  /plan <on|off|toggle>       Plan mode — 先看 LLM 计划再决定 (当前 = " + plan_state + ")\n"
            "  /approve <tool1,tool2|*>    给危险工具发白名单 (当前 = " + approved + ")\n"
            "  /unapprove <tool1|all>      撤回白名单\n"
            "  /memory                     显示当前生效的 AGENTS.md / CLAUDE.md\n"
            "  /history                    查看最近历史\n"
            "  /help                       查看帮助\n"
            "  /exit / /quit               退出控制台\n"
            "或直接输入文本: 按当前 mode 走 RAG / Agent / Hybrid 链路."
        )

    def _generate_trace_id(self) -> str:
        return uuid.uuid4().hex

    def _handle_exception(
        self,
        exception: Exception,
        trace_id: str,
        request: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """统一异常处理(委托给 deps_module.handle_exception_to_envelope)。"""
        return handle_exception_to_envelope(
            exception_handler=self.exception_handler,
            exception=exception,
            trace_id=trace_id,
            fallback_code="UNKNOWN_ERROR",
            fallback_message="控制台执行失败",
            stage="console",
            context={"request": request, "source": "console"},
        )
