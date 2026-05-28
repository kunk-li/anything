from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class ConsoleSessionConfig:
    mode: str = "rag"
    session_id: Optional[str] = None
    top_k: int = 5
    verbose: bool = True
    prompt_text: str = "请输入问题/任务（/help 查看帮助，/exit 退出）："
    renderer: str = "plain"
    attachments: List[str] = field(default_factory=list)
    multiline: bool = False
    # Task X (#58): plan mode + 工具审批 state, 跟 SimpleAgent.extra_params 对齐
    plan_only: bool = False
    approve_tools: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConsoleInput:
    raw_text: str
    cleaned_text: str
    is_command: bool = False
    command_name: Optional[str] = None
    command_arg: Optional[str] = None
    attachment_paths: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConsoleRenderResult:
    success: bool
    title: str
    body: str
    footer: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class ConsoleHistoryItem:
    timestamp: str
    session_id: str
    request: Dict[str, Any]
    response: Dict[str, Any]
    duration_ms: int = 0
    source: str = "interactive"
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
