# -*- coding: utf-8 -*-
"""
AgentRoutesMixin (Task FFFF #123)

Agent 工具元信息端点:
    GET /agent/tools  列出已注册的 agent 工具 (name + description + 分类)

让前端"工具调用" tab 在空态时能展示 Agent 都能干啥, 解决用户报
"Agent 还像聊天工具" 的可见性问题.

self.tool_registry 为 None 时返 SERVICE_UNAVAILABLE 501.
VV (#82) 的 _register_v1_aliases 自动给 /agent/tools 加 /v1 别名.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import Request
from fastapi.responses import JSONResponse


# 工具分类映射 — 让前端能分组展示. 没列到的工具归 "其他".
_TOOL_CATEGORIES: Dict[str, str] = {
    # 信息检索
    "rag_search": "knowledge",
    "wikipedia": "knowledge",
    "web_search": "knowledge",
    "http_get": "knowledge",
    "http_request": "knowledge",
    # 计算 / 文本
    "calculator": "compute",
    "datetime": "compute",
    "currency_convert": "compute",
    "regex_extract": "text",
    "text_stats": "text",
    "json_query": "text",
    "code_lint": "text",
    # 文件 / 系统
    "document_read": "file",
    "file_write": "file",
    "pdf_read": "file",
    "excel_read": "file",
    "sql_query": "knowledge",
    "shell_exec": "system",
    "py_sandbox": "system",
    # LLM / Agent
    "llm_generate": "llm",
    "image_describe": "llm",
    "image_generate": "llm",
    "spawn_subagent": "llm",
    # 通讯 / 外部
    "email_send": "external",
    "weather": "external",
}


class AgentRoutesMixin:
    """Agent 元信息路由 mixin."""

    def _register_agent_routes(self) -> None:
        @self.app.get("/agent/tools")
        async def agent_tools_list(request: Request):
            trace_id = request.state.trace_id
            registry = getattr(self, "tool_registry", None)
            if registry is None:
                return JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "tool_registry 未注入",
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=501,
                )

            try:
                # DictToolRegistry: tools 内部是 dict[name -> (fn, description)]
                names: List[str] = []
                if hasattr(registry, "names"):
                    names = list(registry.names())
                elif hasattr(registry, "list_tools"):
                    names = list(registry.list_tools())
                elif hasattr(registry, "_tools") and isinstance(registry._tools, dict):
                    names = list(registry._tools.keys())

                items = []
                for name in names:
                    desc = ""
                    if hasattr(registry, "describe"):
                        try:
                            desc = registry.describe(name) or ""
                        except Exception:
                            desc = ""
                    if not desc and hasattr(registry, "_tools"):
                        # 直接从内部结构取 description
                        try:
                            t = registry._tools.get(name)
                            if isinstance(t, tuple) and len(t) >= 2:
                                desc = str(t[1])
                            elif isinstance(t, dict):
                                desc = str(t.get("description", ""))
                        except Exception:
                            pass
                    items.append({
                        "name": name,
                        "description": desc,
                        "category": _TOOL_CATEGORIES.get(name, "other"),
                    })
                # 按 category 排序, 再 name; 让 UI 拿到自然分组
                items.sort(key=lambda x: (x["category"], x["name"]))

                # 按类别聚合
                by_category: Dict[str, List[Dict[str, Any]]] = {}
                for it in items:
                    by_category.setdefault(it["category"], []).append(it)

                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": {
                        "count": len(items),
                        "tools": items,
                        "by_category": by_category,
                    },
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "AGENT_TOOLS_LIST_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id,
                     "retryable": False, "details": None},
                    status_code=500,
                )
