# -*- coding: utf-8 -*-
"""
build_interface_layer (Task RR #78 拆出).

接口层装配: 仅一个 RequestHandler. 文件本身只 10 行, 但分文件是为了和
其他 build_X_layer 对齐, 让 RR 后未来加新接口 (gRPC/MCP 等) 有自然落点.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from deps_module import BasicDeps, build_basic_deps
from request_response_module import RequestHandler

from .business_layer import build_business_layer


def build_interface_layer(
    business_layer: Optional[Dict[str, Any]] = None,
    deps: Optional[BasicDeps] = None,
) -> Dict[str, Any]:
    """构建接口层（共享 BasicDeps）"""
    deps = deps or build_basic_deps()
    business_layer = business_layer or build_business_layer(deps=deps)
    handler = RequestHandler(orchestrator=business_layer["orchestrator"], deps=deps)
    return {"handler": handler}
