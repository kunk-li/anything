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
from .mcp_provider import (
    McpServerSpec, McpStdioClient, McpHttpClient, McpToolProvider,
)

__all__ = [
    "ExternalToolProvider", "ExternalToolDef",
    "HttpToolProvider", "HttpToolSpec", "make_http_tool",
    "McpToolProvider", "McpServerSpec", "McpStdioClient", "McpHttpClient",
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


def _specs_from(config: Any, key: str, spec_cls: type, required: tuple) -> list:
    """从 config[key] (dict 列表) 构造 spec_cls 实例列表; 缺必填键/未知键/异常自动过滤。"""
    try:
        raw = config.get_config(key, []) or []
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    fields = set(spec_cls.__dataclass_fields__)
    out = []
    for item in raw:
        if isinstance(item, dict) and all(item.get(r) for r in required):
            try:
                out.append(spec_cls(**{k: v for k, v in item.items() if k in fields}))
            except Exception:
                continue
    return out


def build_providers_from_config(config: Any) -> List[ExternalToolProvider]:
    """从 config 构造外部工具 providers:
      - `agent.external_tools` (HttpToolSpec dict 列表) → HttpToolProvider (Stage1)
      - `agent.mcp_servers`    (McpServerSpec dict 列表) → McpToolProvider (Stage2)
    无配置 / 异常 → []; 未知键自动过滤 (健壮)。
    """
    if config is None:
        return []
    providers: List[ExternalToolProvider] = []
    http_specs = _specs_from(config, "agent.external_tools", HttpToolSpec, ("name", "url"))
    if http_specs:
        providers.append(HttpToolProvider(http_specs))
    mcp_specs = _specs_from(config, "agent.mcp_servers", McpServerSpec, ("name", "command"))
    if mcp_specs:
        providers.append(McpToolProvider(mcp_specs))
    return providers
