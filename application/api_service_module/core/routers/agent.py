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
    "browser_visit": "knowledge",
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

        # 执行计划⑥ (可见性面板): 自维护提议 (默认关, 需 agent.enable_self_reflection) + 人审批执行。
        # 给"待审批维护提议"面板当数据源 + 一键 approve 出口 (方向4 提议终于有消费方)。
        @self.app.get("/agent/maintenance/proposals")
        async def agent_maintenance_proposals(request: Request):
            trace_id = request.state.trace_id
            agent = getattr(self, "agent", None)
            if agent is None:
                return JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "agent 未注入",
                     "data": None, "trace_id": trace_id, "retryable": False, "details": None},
                    status_code=501,
                )
            tenant = self._memory_tenant_from_request(request)
            scope_raw = request.query_params.get("scope") or "memory"
            scope = tuple(s.strip() for s in scope_raw.split(",") if s.strip()) or ("memory",)
            try:
                result = agent.run_maintenance_scan(tenant_id=tenant, trace_id=trace_id, scope=scope)
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok", "data": result,
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "MAINTENANCE_SCAN_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id, "retryable": False, "details": None},
                    status_code=500,
                )

        @self.app.post("/agent/maintenance/apply")
        async def agent_maintenance_apply(request: Request):
            trace_id = request.state.trace_id
            agent = getattr(self, "agent", None)
            if agent is None:
                return JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "agent 未注入",
                     "data": None, "trace_id": trace_id, "retryable": False, "details": None},
                    status_code=501,
                )
            tenant = self._memory_tenant_from_request(request)
            try:
                body = await request.json()
            except Exception:
                body = {}
            proposals = body.get("proposals") or []
            approved_ids = body.get("approved_ids") or []
            try:
                result = agent.apply_memory_maintenance(
                    proposals, approved_ids, tenant_id=tenant, trace_id=trace_id)
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok", "data": result,
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "MAINTENANCE_APPLY_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id, "retryable": False, "details": None},
                    status_code=500,
                )

        # 用户分析流程 (analyze_user, 方向1 深化): 主动分析使用者 → 洞察(只读) + 画像增强提议(审批反哺)。
        # 默认需 agent.enable_user_analysis。给可见性面板"用户洞察"区当数据源 + 反哺出口。
        @self.app.get("/agent/user-analysis")
        async def agent_user_analysis(request: Request):
            trace_id = request.state.trace_id
            agent = getattr(self, "agent", None)
            if agent is None:
                return JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "agent 未注入",
                     "data": None, "trace_id": trace_id, "retryable": False, "details": None},
                    status_code=501)
            tenant = self._memory_tenant_from_request(request)
            try:
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok",
                    "data": agent.analyze_user(tenant_id=tenant, trace_id=trace_id),
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "USER_ANALYSIS_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id, "retryable": False, "details": None},
                    status_code=500)

        @self.app.post("/agent/user-analysis/apply")
        async def agent_user_analysis_apply(request: Request):
            trace_id = request.state.trace_id
            agent = getattr(self, "agent", None)
            if agent is None:
                return JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "agent 未注入",
                     "data": None, "trace_id": trace_id, "retryable": False, "details": None},
                    status_code=501)
            tenant = self._memory_tenant_from_request(request)
            try:
                body = await request.json()
            except Exception:
                body = {}
            try:
                result = agent.apply_user_insights(
                    body.get("proposals") or [], body.get("approved_ids") or [],
                    tenant_id=tenant, trace_id=trace_id)
                return JSONResponse({
                    "code": "SUCCESS", "message": "ok", "data": result,
                    "trace_id": trace_id, "retryable": False, "details": None,
                })
            except Exception as e:
                return JSONResponse(
                    {"code": "USER_ANALYSIS_APPLY_FAILED", "message": str(e),
                     "data": None, "trace_id": trace_id, "retryable": False, "details": None},
                    status_code=500)

        # 执行计划⑦ (统一配置界面 + 能力档位): 读/改 agent 开关 (运行期 live) + 一键档位。
        # 消费②的配置 schema 当数据源。⚠️ 能改自主能力开关, 生产应在网关加 admin 鉴权 (同 /config/models)。
        def _agent_or_503(trace_id):
            agent = getattr(self, "agent", None)
            if agent is None:
                return None, JSONResponse(
                    {"code": "SERVICE_UNAVAILABLE", "message": "agent 未注入",
                     "data": None, "trace_id": trace_id, "retryable": False, "details": None},
                    status_code=501)
            return agent, None

        @self.app.get("/config/agent")
        async def config_agent_get(request: Request):
            trace_id = request.state.trace_id
            agent, err = _agent_or_503(trace_id)
            if err:
                return err
            from agent_module.config.schema import dump_agent_config
            return JSONResponse({
                "code": "SUCCESS", "message": "ok",
                "data": {"fields": dump_agent_config(self.config, agent)},
                "trace_id": trace_id, "retryable": False, "details": None,
            })

        @self.app.post("/config/agent")
        async def config_agent_set(request: Request):
            trace_id = request.state.trace_id
            agent, err = _agent_or_503(trace_id)
            if err:
                return err
            from agent_module.config.schema import apply_agent_config, dump_agent_config
            try:
                body = await request.json()
            except Exception:
                body = {}
            updates = body.get("updates") if isinstance(body, dict) else None
            if not isinstance(updates, dict):
                updates = body if isinstance(body, dict) else {}
            result = apply_agent_config(agent, updates)
            result["fields"] = dump_agent_config(self.config, agent)
            return JSONResponse({
                "code": "SUCCESS", "message": "ok", "data": result,
                "trace_id": trace_id, "retryable": False, "details": None,
            })

        @self.app.get("/config/agent/presets")
        async def config_agent_presets(request: Request):
            trace_id = request.state.trace_id
            from agent_module.config.schema import AGENT_CONFIG_PRESETS
            return JSONResponse({
                "code": "SUCCESS", "message": "ok",
                "data": {"presets": AGENT_CONFIG_PRESETS},
                "trace_id": trace_id, "retryable": False, "details": None,
            })

        @self.app.post("/config/agent/preset")
        async def config_agent_apply_preset(request: Request):
            trace_id = request.state.trace_id
            agent, err = _agent_or_503(trace_id)
            if err:
                return err
            from agent_module.config.schema import apply_preset, dump_agent_config
            try:
                body = await request.json()
            except Exception:
                body = {}
            name = (body or {}).get("name") or (body or {}).get("preset")
            result = apply_preset(agent, name)
            result["fields"] = dump_agent_config(self.config, agent)
            return JSONResponse({
                "code": "SUCCESS", "message": "ok", "data": result,
                "trace_id": trace_id, "retryable": False, "details": None,
            })
