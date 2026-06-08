# -*- coding: utf-8 -*-
"""
OpenAPI → HTTP 外部工具自动生成 (外部工具连接 RFC · 增量).

给一份 OpenAPI(dict), 把每个 path×method(operation) 自动转成一个 `HttpToolSpec` ——
复用 Stage1 的 `HttpToolProvider` 注册执行。映射:
    operationId          → 工具名 (可加 name_prefix)
    path 参数 {id}        → url 占位 (HttpToolSpec.url 用 payload 填)
    query 参数           → HttpToolSpec.query
    requestBody schema   → HttpToolSpec.body (JSON body 键列表)
    servers[0].url       → base_url 缺省

仅声明式映射, 不做复杂 schema 校验。生成的工具同样默认需审批 + 复用 SSRF 防御。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from .http_provider import HttpToolSpec

_METHODS = ("get", "post", "put", "patch", "delete")


def fetch_openapi_spec(
    url: str,
    timeout: int = 10,
    max_bytes: int = 2 * 1024 * 1024,
    headers: Optional[Dict[str, str]] = None,
    fetch: Optional[Callable] = None,
) -> Dict[str, Any]:
    """远程拉取 OpenAPI spec 并解析成 dict (外部工具连接 RFC · spec_url 远程拉取)。

    复用 http_provider 的 **SSRF 校验** + 禁跳转 fetch (拒私网/非 http(s)); 优先按 JSON 解析,
    失败再退化尝试 YAML (PyYAML 可用时)。fetch 可注入 (测试不发真网络)。
    失败抛 ValueError/RuntimeError, 由调用方 (build_providers_from_config) fail-safe 跳过该项。
    """
    from .http_provider import _url_ssrf_error, _default_fetch
    err = _url_ssrf_error(url)
    if err:
        raise ValueError(f"spec_url 不安全 (SSRF 防御): {err}")
    do_fetch = fetch or _default_fetch
    req_headers = {"User-Agent": "anything-agent/1.0",
                   "Accept": "application/json, application/yaml, text/yaml", **(headers or {})}
    status, text = do_fetch("GET", url, req_headers, None, timeout, max_bytes)
    if not (200 <= int(status) < 300):
        raise RuntimeError(f"拉取 spec_url 失败: status={status}")
    spec: Any = None
    try:
        spec = json.loads(text)
    except ValueError:
        try:
            import yaml  # PyYAML (本项目 config 已用, 一般可用); 不可用则放弃 YAML 退化
            spec = yaml.safe_load(text)
        except Exception:
            raise ValueError("spec_url 内容既非合法 JSON 也无法按 YAML 解析")
    if not isinstance(spec, dict):
        raise ValueError("spec_url 解析结果顶层不是对象")
    return spec


def _first_server_url(openapi: Dict[str, Any]) -> str:
    servers = openapi.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        return str(servers[0].get("url") or "")
    return ""


def _derive_op_id(method: str, path: str) -> str:
    slug = "".join(c if c.isalnum() else "_" for c in path).strip("_")
    return f"{method}_{slug}" if slug else method


def _request_body_props(request_body: Any) -> List[str]:
    """从 requestBody 的 application/json schema 取属性名作为 JSON body 键。无则 []。"""
    if not isinstance(request_body, dict):
        return []
    content = request_body.get("content") or {}
    schema = (content.get("application/json") or {}).get("schema") if isinstance(content, dict) else None
    props = schema.get("properties") if isinstance(schema, dict) else None
    return list(props.keys()) if isinstance(props, dict) else []


def openapi_to_specs(
    openapi: Dict[str, Any],
    base_url: str = "",
    name_prefix: str = "",
    auth_header: str = "",
    auth_value: str = "",
    default_timeout: int = 10,
) -> List[HttpToolSpec]:
    """OpenAPI(dict) → List[HttpToolSpec] (每个 operation 一个工具)。非法/空 → []。"""
    if not isinstance(openapi, dict):
        return []
    paths = openapi.get("paths")
    if not isinstance(paths, dict):
        return []
    base = (base_url or _first_server_url(openapi)).rstrip("/")
    specs: List[HttpToolSpec] = []
    for path, item in paths.items():
        if not isinstance(item, dict) or not isinstance(path, str):
            continue
        for method in _METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            op_id = str(op.get("operationId") or _derive_op_id(method, path))
            params = op.get("parameters") or []
            query = [p.get("name") for p in params
                     if isinstance(p, dict) and p.get("in") == "query" and p.get("name")]
            body = _request_body_props(op.get("requestBody"))
            specs.append(HttpToolSpec(
                name=f"{name_prefix}{op_id}" if name_prefix else op_id,
                url=base + path,                          # path 含 {id} → 由 HttpToolSpec url 占位填充
                method=method.upper(),
                description=str(op.get("summary") or op.get("description")
                                or f"{method.upper()} {path}")[:200],
                query=query, body=body,
                auth_header=auth_header, auth_value=auth_value,
                timeout=default_timeout, requires_approval=True,
            ))
    return specs
