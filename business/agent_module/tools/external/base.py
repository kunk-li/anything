# -*- coding: utf-8 -*-
"""
外部工具接入抽象 (外部工具连接 RFC).

让 agent 接入"内置硬编码工具"之外的外部工具提供方。`ExternalToolProvider` 是统一抽象:
HTTP/OpenAPI 连接器(Stage1) 与 MCP 客户端(Stage2) 都实现它 —— 启动时各 provider
`discover()` → 注册进 `tool_registry`。新增 provider 不动 agent / registry。

安全姿态 (RFC §6): 外部工具默认 `requires_approval=True` (并入 agent 审批白名单,
须 `extra_params.approve_tools` 才执行), human-in-loop。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


@dataclass
class ExternalToolDef:
    """一个外部工具的注册定义 (registry-ready)。"""
    name: str
    func: Callable[[Dict[str, Any]], Dict[str, Any]]   # payload(dict) -> 标准响应 envelope
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = True   # 外部工具默认需审批 (human-in-loop 安全姿态)
    source: str = "external"         # provider 来源标记 (审计/区分内置 vs 外部)


class ExternalToolProvider(ABC):
    """外部工具提供方抽象。HTTP/OpenAPI(Stage1) / MCP(Stage2) 都实现此契约。"""

    @abstractmethod
    def discover(self) -> List[ExternalToolDef]:
        """发现该 provider 暴露的工具 (注册前调用一次)。

        实现须 **fail-safe**: 连接/解析失败应返回 `[]` 而非抛异常, 不拖垮启动。
        """
        ...
