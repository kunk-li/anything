# -*- coding: utf-8 -*-
"""外部工具接入 (外部工具连接 RFC). Stage1: HTTP/OpenAPI 连接器; Stage2(未来): MCP 客户端。

启动接线 (run/factories/business_layer.py):
    providers = build_providers_from_config(deps.config)
    approval_names = register_external_tools(tool_registry, providers)
    agent.tool_approval_required.update(approval_names)   # 外部工具默认需审批
"""
from __future__ import annotations

from typing import Any, List, Optional, Set

from .base import ExternalToolDef, ExternalToolProvider
from .http_provider import HttpToolProvider, HttpToolSpec, make_http_tool

__all__ = [
    "ExternalToolProvider", "ExternalToolDef",
    "HttpToolProvider", "HttpToolSpec", "make_http_tool",
    "register_external_tools", "build_providers_from_config",
]


def register_external_tools(
    registry: Any,
    providers: List[ExternalToolProvider],
    approval_set: Optional[Set[str]] = None,
) -> List[str]:
    """各 provider `discover()` → 注册进 registry; 返回**需审批**的外部工具名列表。
    approval_set 给定时直接加入。fail-safe: 单个 provider/工具异常跳过, 不阻断。"""
    needing_approval: List[str] = []
    for p in providers:
        try:
            defs = p.discover()
        except Exception:
            continue
        for td in defs:
            try:
                registry.register(td.name, td.func, description=td.description)
            except Exception:
                continue
            if td.requires_approval:
                needing_approval.append(td.name)
                if approval_set is not None:
                    approval_set.add(td.name)
    return needing_approval


def build_providers_from_config(config: Any) -> List[ExternalToolProvider]:
    """从 config `agent.external_tools` (HttpToolSpec dict 列表) 构造 HttpToolProvider。
    无配置 / 异常 → []。未知键自动过滤 (健壮)。"""
    if config is None:
        return []
    try:
        raw = config.get_config("agent.external_tools", []) or []
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    fields = set(HttpToolSpec.__dataclass_fields__)
    specs: List[HttpToolSpec] = []
    for item in raw:
        if isinstance(item, dict) and item.get("name") and item.get("url"):
            try:
                specs.append(HttpToolSpec(**{k: v for k, v in item.items() if k in fields}))
            except Exception:
                continue
    return [HttpToolProvider(specs)] if specs else []
