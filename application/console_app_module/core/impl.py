# -*- coding: utf-8 -*-
"""
控制台交互模块具体实现类
负责控制台输入、批处理、统一请求构造、调用接口层与终端输出渲染
"""

import json
import time
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional

from .base import BaseConsoleApp

from deps_module import BasicDeps, build_basic_deps


class ConsoleApp(BaseConsoleApp):
    """标准控制台实现：run_once / run_interactive / run_batch"""

    def __init__(
        self,
        handler,
        input_provider=None,
        renderer=None,
        history_store=None,
        deps: Optional[BasicDeps] = None,
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
        self.history_store = history_store

        self.default_render_mode = self.config.get_config(
            "console_app.default_render_mode", "friendly"
        )
        self.enable_history = bool(self.config.get_config("console_app.enable_history", True))
        self.history_file = self.config.get_config(
            "console_app.history_file", "./console_history.jsonl"
        )
        self.batch_fail_fast = bool(self.config.get_config("console_app.batch_fail_fast", False))

        self.logger.info("控制台交互模块初始化完成")

    # =========================
    # 抽象接口实现
    # =========================
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

            result = self.handler.handle(standardized, trace_id=trace_id)

            render_text = self.render_response(result)
            if render_text:
                print(render_text)

            if self.enable_history:
                self._save_history_safe(
                    trace_id=trace_id,
                    request=standardized,
                    response=result,
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
                print(self.render_response(error_response))
            except Exception:
                pass
            return error_response

    def run_interactive(self) -> None:
        """进入交互式控制台模式"""
        self.logger.info("进入控制台交互模式")
        print("进入交互模式。输入 help 查看帮助，输入 exit 或 quit 退出。")

        while True:
            try:
                raw = self._read_input(">>> ")
                if raw is None:
                    continue

                raw = raw.strip()
                if not raw:
                    continue

                if raw.lower() in {"exit", "quit"}:
                    print("已退出控制台。")
                    break

                if raw.lower() == "help":
                    print(self._help_text())
                    continue

                if raw.lower() == "history":
                    print(self._render_history())
                    continue

                request = self._parse_interactive_input(raw)
                self.run_once(request)

            except KeyboardInterrupt:
                print("\n已退出控制台。")
                break
            except Exception as e:
                self.logger.error(f"交互模式异常：{str(e)}")
                print(f"[ERROR] 控制台输入处理失败: {str(e)}")

    def run_batch(self, file_path: str) -> Dict[str, Any]:
        """执行批处理文件"""
        path = Path(file_path)
        trace_id = self._generate_trace_id()

        if not path.exists():
            return {
                "code": "BATCH_FILE_NOT_FOUND",
                "message": f"批处理文件不存在：{file_path}",
                "data": None,
                "trace_id": trace_id,
                "retryable": False,
                "details": {"path": file_path},
            }

        success_count = 0
        failed_count = 0
        results: List[Dict[str, Any]] = []

        try:
            if path.suffix.lower() == ".jsonl":
                items = self._load_jsonl_batch(path)
            else:
                items = self._load_text_batch(path)

            for item in items:
                item_trace_id = item.get("trace_id") or self._generate_trace_id()
                try:
                    result = self.run_once(item, trace_id=item_trace_id)
                    results.append(result)
                    if result.get("code") == "SUCCESS":
                        success_count += 1
                    else:
                        failed_count += 1
                        if self.batch_fail_fast:
                            break
                except Exception as e:
                    failed_count += 1
                    error_item = {
                        "code": "UNKNOWN_ERROR",
                        "message": str(e),
                        "data": None,
                        "trace_id": item_trace_id,
                        "retryable": False,
                        "details": {"path": file_path},
                    }
                    results.append(error_item)
                    if self.batch_fail_fast:
                        break

            summary = {
                "code": "SUCCESS",
                "message": "batch completed",
                "data": {
                    "success_count": success_count,
                    "failed_count": failed_count,
                    "results": results,
                },
                "trace_id": trace_id,
                "retryable": False,
                "details": None,
            }

            print(self.render_response(summary))
            return summary

        except Exception as e:
            self.logger.error(f"批处理执行异常：path={file_path}, error={str(e)}")
            return {
                "code": "BATCH_FILE_INVALID",
                "message": f"批处理文件格式非法：{str(e)}",
                "data": None,
                "trace_id": trace_id,
                "retryable": False,
                "details": {"path": file_path},
            }

    # =========================
    # 对外辅助能力
    # =========================
    def render_response(self, response: Dict[str, Any], mode: Optional[str] = None) -> str:
        """渲染统一响应为终端文本"""
        mode = mode or self.default_render_mode

        if self.renderer is not None and hasattr(self.renderer, "render"):
            return self.renderer.render(response, mode=mode)

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
        """读取输入，优先使用注入的 input_provider"""
        if self.input_provider is not None:
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

    def _save_history_safe(self, trace_id: str, request: Dict[str, Any], response: Dict[str, Any]) -> None:
        """安全保存本地历史，不阻断主流程"""
        if not self.enable_history:
            return

        item = {
            "trace_id": trace_id,
            "request": request,
            "response": response,
            "created_at": time.time(),
        }

        try:
            if self.history_store is not None:
                if hasattr(self.history_store, "save"):
                    self.history_store.save(item)
                elif hasattr(self.history_store, "append"):
                    self.history_store.append(item)
                elif callable(self.history_store):
                    self.history_store(item)
                return

            history_path = Path(self.history_file)
            history_path.parent.mkdir(parents=True, exist_ok=True)
            with history_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception as e:
            self.logger.warning(f"控制台历史保存失败（已忽略）：trace_id={trace_id}, error={str(e)}")

    def _render_history(self) -> str:
        """渲染本地历史"""
        history_path = Path(self.history_file)
        if not history_path.exists():
            return "暂无历史记录。"

        lines = []
        for line in history_path.read_text(encoding="utf-8").splitlines()[-20:]:
            try:
                item = json.loads(line)
                trace_id = item.get("trace_id")
                request = item.get("request", {})
                lines.append(f"{trace_id} | {request.get('type')} | {request.get('query') or request.get('task')}")
            except Exception:
                continue

        return "\n".join(lines) if lines else "暂无历史记录。"

    def _help_text(self) -> str:
        return (
            "可用命令：\n"
            "  rag: <问题>       以 RAG 模式执行\n"
            "  agent: <任务>     以 Agent 模式执行\n"
            "  hybrid: <任务>    以 Hybrid 模式执行\n"
            "  history           查看最近历史\n"
            "  help              查看帮助\n"
            "  exit / quit       退出控制台"
        )

    def _generate_trace_id(self) -> str:
        return uuid.uuid4().hex

    def _handle_exception(
        self,
        exception: Exception,
        trace_id: str,
        request: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """统一异常处理"""
        try:
            if hasattr(self.exception_handler, "handle"):
                error_info = self.exception_handler.handle(exception, trace_id=trace_id)
            else:
                error_info = self.exception_handler.handle_exception(exception)

            return {
                "code": error_info.get("code", "UNKNOWN_ERROR"),
                "message": error_info.get("message", "控制台执行失败"),
                "data": None,
                "trace_id": trace_id,
                "retryable": error_info.get("retryable", False),
                "details": error_info.get("details") or {
                    "source": "console",
                    "request": request,
                },
            }
        except Exception:
            return {
                "code": "UNKNOWN_ERROR",
                "message": "控制台执行失败",
                "data": None,
                "trace_id": trace_id,
                "retryable": False,
                "details": {
                    "source": "console",
                    "request": request,
                },
            }
