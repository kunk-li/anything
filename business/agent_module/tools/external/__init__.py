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
from .openapi import openapi_to_specs, fetch_openapi_spec

__all__ = [
    "ExternalToolProvider", "ExternalToolDef",
    "HttpToolProvider", "HttpToolSpec", "make_http_tool",
    "McpToolProvider", "McpServerSpec", "McpStdioClient", "McpHttpClient",
    "openapi_to_specs", "fetch_openapi_spec",
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


def _mcp_specs_from(config: Any) -> list:
    """从 config `agent.mcp_servers` 构造 McpServerSpec 列表 (transport 感知必填校验)。

    `_specs_from` 的固定 required 元组对 MCP 不成立: stdio 必填 `command`, http 必填 `url`
    (二选一, 取决于 `transport`)。统一要求 `command` 会让 **http MCP server 永远被静默丢弃**
    (死功能)。这里按 `transport` 判定: name + (stdio→command / http→url)。未知键/异常自动过滤。"""
    try:
        raw = config.get_config("agent.mcp_servers", []) or []
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    fields = set(McpServerSpec.__dataclass_fields__)
    out = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        transport = item.get("transport", "stdio")
        endpoint_key = "url" if transport == "http" else "command"
        if not item.get(endpoint_key):          # 传输对应的端点必填 (stdio:command / http:url)
            continue
        try:
            out.append(McpServerSpec(**{k: v for k, v in item.items() if k in fields}))
        except Exception:
            continue
    return out


def _openapi_specs_from(config: Any) -> list:
    """从 config `agent.openapi_tools` 生成 HttpToolSpec 列表。每项二选一取 spec 来源:
      - inline `spec`: OpenAPI dict (本地直接给);
      - `spec_url`:    远程 OpenAPI 地址 (SSRF 安全拉取 + JSON/YAML 解析)。
    其余键 base_url/name_prefix/auth_* 同。拉取/解析/生成异常自动跳过该项 (fail-safe)。"""
    try:
        raw = config.get_config("agent.openapi_tools", []) or []
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        spec = entry.get("spec")
        if not isinstance(spec, dict):
            # spec_url 远程拉取 (inline spec 缺省时)。拉取用 entry 的 auth (若有), fail-safe。
            spec_url = entry.get("spec_url")
            if not (isinstance(spec_url, str) and spec_url.strip()):
                continue
            _fetch_headers = {}
            if entry.get("auth_header") and entry.get("auth_value"):
                _fetch_headers[entry["auth_header"]] = entry["auth_value"]
            try:
                spec = fetch_openapi_spec(spec_url.strip(), headers=_fetch_headers or None)
            except Exception:
                continue
        try:
            out.extend(openapi_to_specs(
                spec,
                base_url=entry.get("base_url", ""),
                name_prefix=entry.get("name_prefix", ""),
                auth_header=entry.get("auth_header", ""),
                auth_value=entry.get("auth_value", ""),
            ))
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
    http_specs += _openapi_specs_from(config)          # OpenAPI 自动生成的 HTTP 工具
    if http_specs:
        providers.append(HttpToolProvider(http_specs))
    mcp_specs = _mcp_specs_from(config)                # transport 感知: stdio→command / http→url
    if mcp_specs:
        providers.append(McpToolProvider(mcp_specs))
    return providers
