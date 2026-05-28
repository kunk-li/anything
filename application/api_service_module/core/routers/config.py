# -*- coding: utf-8 -*-
"""
ConfigRoutesMixin (Task LL #72)
GET    /config/models                       列已注册 LLM 模型 (api_key 脱敏)
POST   /config/models                       注册/更新模型
DELETE /config/models/{name}                注销模型
POST   /config/models/{name}/set-default    设为对应 request_type 的默认

⚠️ 生产部署在网关层屏蔽 /config/* (除非已加 admin RBAC).
"""

from __future__ import annotations

import traceback

from fastapi import Request
from fastapi.responses import JSONResponse


class ConfigRoutesMixin:
    """LLM 模型运行期注册表路由."""

    def _register_config_routes(self) -> None:
        # ============== LLM 模型管理 (运行期注册表) ==============
        # 客户端能在 Web UI 里编辑模型 + key, 不持久化到 yaml (重启丢失)。
        # ⚠️ 生产部署在网关层屏蔽 /config/* (除非你已加 admin RBAC)。

        def _need_llm_service():
            if self.llm_service is None:
                return JSONResponse(
                    status_code=501,
                    content={
                        "code": "SERVICE_UNAVAILABLE",
                        "message": "llm_service 未注入, 模型管理端点不可用",
                        "data": None,
                        "trace_id": None,
                        "retryable": False,
                        "details": None,
                    },
                )
            return None

        @self.app.get("/config/models")
        async def list_models(request: Request):
            err = _need_llm_service()
            if err is not None:
                return err
            trace_id = request.state.trace_id
            try:
                models = self.llm_service.list_models(mask_keys=True)
            except Exception as e:
                self.logger.error(f"list_models failed: {e}\n{traceback.format_exc()}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "code": "UNKNOWN_ERROR",
                        "message": str(e),
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )
            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "ok",
                    "data": {"models": models},
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.post("/config/models")
        async def register_model(request: Request):
            err = _need_llm_service()
            if err is not None:
                return err
            trace_id = request.state.trace_id
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "BAD_REQUEST",
                        "message": "请求体不是合法 JSON",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )

            name = (body.get("name") or "").strip()
            request_type = (body.get("request_type") or "").upper()
            adapter_class = (body.get("adapter_class") or "").strip()
            api_key = body.get("api_key") or ""
            api_base = (body.get("api_base") or "").strip()
            set_as_default = bool(body.get("set_as_default", False))

            if not name or not request_type or not adapter_class:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "PARAM_MISSING",
                        "message": "name / request_type / adapter_class 必填",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": {
                            "name": bool(name),
                            "request_type": bool(request_type),
                            "adapter_class": bool(adapter_class),
                        },
                    },
                    headers={"X-Request-Id": trace_id},
                )
            try:
                entry = self.llm_service.register_or_update_model(
                    name=name,
                    request_type=request_type,
                    adapter_class=adapter_class,
                    api_key=api_key,
                    api_base=api_base,
                    set_as_default=set_as_default,
                )
            except ValueError as ve:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "PARAM_INVALID",
                        "message": str(ve),
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )
            except Exception as e:
                self.logger.error(f"register_model failed: {e}\n{traceback.format_exc()}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "code": "UNKNOWN_ERROR",
                        "message": str(e),
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )
            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "model registered",
                    "data": entry,
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.delete("/config/models/{name}")
        async def delete_model(name: str, request: Request):
            err = _need_llm_service()
            if err is not None:
                return err
            trace_id = request.state.trace_id
            existed = self.llm_service.unregister_model(name)
            return JSONResponse(
                status_code=200 if existed else 404,
                content={
                    "code": "SUCCESS" if existed else "MODEL_NOT_FOUND",
                    "message": "removed" if existed else "model 不存在",
                    "data": {"name": name, "existed": existed},
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.post("/config/models/{name}/set-default")
        async def set_default_model(name: str, request: Request):
            err = _need_llm_service()
            if err is not None:
                return err
            trace_id = request.state.trace_id
            try:
                body = await request.json()
            except Exception:
                body = {}
            request_type = (body.get("request_type") or "") if isinstance(body, dict) else ""
            try:
                result = self.llm_service.set_default_model(name=name, request_type=request_type)
            except ValueError as ve:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "PARAM_INVALID",
                        "message": str(ve),
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )
            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "default updated",
                    "data": result,
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

