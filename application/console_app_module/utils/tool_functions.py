from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from console_app_module.model.data_model import ConsoleInput, ConsoleSessionConfig


VALID_MODES = {"rag", "agent", "hybrid"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_session_id() -> str:
    return uuid.uuid4().hex


def clean_input(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_command(text: str) -> ConsoleInput:
    raw = text.rstrip("\n")
    cleaned = raw.strip()
    if cleaned.startswith("/"):
        body = cleaned[1:]
        if not body:
            return ConsoleInput(raw_text=raw, cleaned_text=cleaned)
        parts = body.split(maxsplit=1)
        command_name = parts[0].lower()
        command_arg = parts[1].strip() if len(parts) > 1 else None
        return ConsoleInput(
            raw_text=raw,
            cleaned_text=cleaned,
            is_command=True,
            command_name=command_name,
            command_arg=command_arg,
        )
    return ConsoleInput(raw_text=raw, cleaned_text=clean_input(raw))


def ensure_mode(mode: str) -> str:
    m = (mode or "").strip().lower()
    if m not in VALID_MODES:
        raise ValueError(f"非法 mode: {mode}")
    return m


def build_request_dict(console_input: ConsoleInput, session: ConsoleSessionConfig) -> Dict[str, Any]:
    mode = ensure_mode(session.mode)
    session_id = session.session_id or generate_session_id()
    text = console_input.cleaned_text
    request: Dict[str, Any] = {
        "type": mode,
        "session_id": session_id,
        "top_k": int(session.top_k),
        "extra_params": {
            "attachments": list(session.attachments) + list(console_input.attachment_paths),
            "source": "console_app",
            "console_meta": {
                "renderer": session.renderer,
                "verbose": session.verbose,
                "multiline": session.multiline,
            },
        },
    }
    if mode == "rag":
        request["query"] = text
    else:
        request["task"] = text
    return request


def format_response_text(response: Dict[str, Any], verbose: bool = True) -> str:
    code = response.get("code", "UNKNOWN")
    message = response.get("message", "")
    data = response.get("data")
    trace_id = response.get("trace_id")
    cost_time = response.get("cost_time")
    lines: List[str] = [f"code: {code}", f"message: {message}"]
    if data is not None:
        if isinstance(data, dict) and "answer" in data:
            lines.append(f"answer: {data['answer']}")
        elif verbose:
            lines.append("data: " + json.dumps(data, ensure_ascii=False, indent=2))
    if verbose and trace_id:
        lines.append(f"trace_id: {trace_id}")
    if verbose and cost_time is not None:
        lines.append(f"cost_time: {cost_time}")
    return "\n".join(lines)


def load_batch_file(path: str) -> List[Dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"批处理文件不存在: {path}")
    suffix = file_path.suffix.lower()
    if suffix == ".jsonl":
        return [json.loads(line) for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if suffix == ".json":
        data = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        raise ValueError("json 批处理文件必须为列表")
    if suffix in {".txt", ".md"}:
        return [{"text": line.strip()} for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError("解析 yaml 需要安装 pyyaml") from exc
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        raise ValueError("yaml 批处理文件必须为列表")
    raise ValueError(f"不支持的批处理文件格式: {suffix}")


def iter_script_lines(path: str) -> Iterable[str]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"脚本文件不存在: {path}")
    text = file_path.read_text(encoding="utf-8")
    block: List[str] = []
    multiline = False
    for raw in text.splitlines():
        stripped = raw.rstrip()
        if not stripped.strip() or stripped.lstrip().startswith("#"):
            continue
        if stripped.strip() == "---":
            if multiline and block:
                yield "\n".join(block)
                block = []
            multiline = not multiline
            continue
        if multiline:
            block.append(stripped)
            continue
        yield stripped
    if block:
        yield "\n".join(block)
