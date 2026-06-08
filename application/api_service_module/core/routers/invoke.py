# -*- coding: utf-8 -*-
"""
InvokeRoutesMixin (Task LL #72)
POST /invoke + WebSocket /invoke/stream
"""

from __future__ import annotations

import asyncio
import json
import time
import traceback

from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from observability_module import (
    trace_span,
    set_current_tenant,
    reset_current_tenant,
)


class InvokeRoutesMixin:
    """主入口路由 (RAG / Agent / Hybrid 同步 + 流式)."""

    def _register_invoke_routes(self) -> None:
        @self.app.post("/invoke")
        async def invoke(request: Request):
            trace_id = request.state.trace_id
            start_time = time.time()

            # 1. 鉴权（协议层）
            auth_error = self._check_auth(request, trace_id)
            if auth_error is not None:
                return auth_error

            # 2. 请求体大小限制（协议层）
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > self.max_body_size:
                        return JSONResponse(
                            status_code=413,
                            content={
                                "code": "PARAM_INVALID",
                                "message": f"请求体过大，超过限制 {self.max_body_size} 字节",
                                "data": None,
                                "trace_id": trace_id,
                                "retryable": False,
                                "details": {
                                    "field": "content-length",
                                    "expected": f"<= {self.max_body_size}",
                                    "actual": content_length,
                                },
                            },
                            headers={"X-Request-Id": trace_id},
                        )
                except ValueError:
                    pass

            # 3. 解析 JSON（协议层）
            try:
                body = await request.json()
            except json.JSONDecodeError:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "BAD_REQUEST",
                        "message": "请求体不是合法 JSON",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": {
                            "field": "body",
                            "expected": "application/json",
                            "actual": "invalid json",
                        },
                    },
                    headers={"X-Request-Id": trace_id},
                )

            if not isinstance(body, dict):
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "BAD_REQUEST",
                        "message": "请求体必须是 JSON 对象",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": {
                            "field": "body",
                            "expected": "json object",
                            "actual": str(type(body).__name__),
                        },
                    },
                    headers={"X-Request-Id": trace_id},
                )

            # 3.5 tenant_id 冲突处理 (Task #33 PR2, 见 docs/multi-tenancy-design.md §4.3)
            #     auth_tenant_id (认证产物) 优先于 body tenant_id (声明)
            self._reconcile_tenant_id(request, body, trace_id)

            # PR4a (§7.3.1): 把 reconcile 后的 tenant_id 注入 ContextVar,
            # 让所有 trace_span / 业务日志能自动拿到. body 没声明就用 default。
            effective_tid = str(body.get("tenant_id") or "default")

            # PR4b: 未知 tenant -> 404 TENANT_NOT_FOUND (§9.3 防枚举, 跟 DOCUMENT_NOT_FOUND 同桶)
            if not self._is_known_tenant(effective_tid):
                self.logger.warning(
                    f"[security] unknown tenant_id={effective_tid!r} trace_id={trace_id}"
                )
                duration = time.time() - start_time
                self._record_metrics(
                    req_type=str(body.get("type", "unknown")),
                    code="TENANT_NOT_FOUND",
                    duration=duration,
                    tenant_id=effective_tid,
                )
                return JSONResponse(
                    status_code=404,
                    content={
                        "code": "TENANT_NOT_FOUND",
                        "message": "tenant 不存在或无权访问",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,  # §9.3: 不暴露存在性
                    },
                    headers={"X-Request-Id": trace_id},
                )

            # PR4b: per-tenant QPS 滑窗限流 (§8 沿用 API_RATE_LIMITED 429)
            if not self._check_qps_quota(effective_tid):
                duration = time.time() - start_time
                self._record_metrics(
                    req_type=str(body.get("type", "unknown")),
                    code="API_RATE_LIMITED",
                    duration=duration,
                    tenant_id=effective_tid,
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "code": "API_RATE_LIMITED",
                        "message": "请求频率超出租户配额, 请稍后重试",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": True,
                        "details": {"tenant_id": effective_tid},
                    },
                    headers={"X-Request-Id": trace_id, "Retry-After": "1"},
                )

            tenant_token = set_current_tenant(effective_tid)
            try:
                # 4. 透传到接口层 (OTel root span 包住整个请求)
                with trace_span(
                    "api.invoke",
                    attributes={
                        "http.method": "POST",
                        "http.route": "/invoke",
                        "anything.trace_id": trace_id,
                        "anything.request_type": str(body.get("type", "unknown")),
                    },
                ) as span:
                    result = self.handler.handle(body, trace_id=trace_id)
                    span.set_attribute(
                        "anything.response_code", str(result.get("code", "UNKNOWN_ERROR"))
                    )

                # 5. 应用层只负责业务码 -> HTTP 状态码映射
                http_status = self._map_code_to_http_status(result.get("code", "UNKNOWN_ERROR"))
                duration = time.time() - start_time
                headers = {"X-Request-Id": trace_id}
                headers["X-Cost-Time"] = str(round(duration, 3))

                # 6. 记录 metrics (异步级别开销极低), 带 tenant 维度 + cardinality 守护
                self._record_metrics(
                    req_type=str(body.get("type", "unknown")),
                    code=str(result.get("code", "UNKNOWN_ERROR")),
                    duration=duration,
                    tenant_id=effective_tid,
                )

                return JSONResponse(
                    status_code=http_status,
                    content=result,
                    headers=headers,
                )
            finally:
                # 无论成功 / 异常, 都必须 reset ContextVar, 防泄漏到下个请求
                reset_current_tenant(tenant_token)

        @self.app.websocket("/invoke/stream")
        async def invoke_stream(ws: WebSocket):
            """WebSocket 流式回答端点 (V1: 简化版 — 后端先拿完整答案再切片发).

            协议:
                Client -> Server (一次性 send_json):
                    RequestEnvelope (同 POST /invoke), 可带 X-API-Key
                    认证头通过 query string 'api_key' 或子协议传递 (浏览器 ws 不支持自定义 header)
                Server -> Client (流式 send_json):
                    {type: 'start',     trace_id, tenant_id}
                    {type: 'chunk',     text}              # 重复 N 次
                    {type: 'metadata',  citations, retrieved_chunks, steps}
                    {type: 'done',      code, cost_time, trace_id}
                    {type: 'error',     code, message}    # 出错时替代 done

            限制 (V1 / TODO 改造为真实 LLM 流式):
                - 实际是同步跑完 handler.handle 后才切片, total 延迟跟非流式一致
                - 当 LLMService 升级到 token stream 后, 这里把 handler 换成 handler.handle_stream 即可

            参考: docs/multi-tenancy-design.md §7.3 OTel 在 WS 上的传播本期不实现
                  (浏览器 WS API 没自定义 header, 后续需要换 querystring 模式)
            """
            # 鉴权 (querystring 风格, 因为浏览器 WS 不能自定义 header)
            api_key_qs = ws.query_params.get("api_key", "")
            if self.auth_enabled and self.auth_type == "apikey":
                if not api_key_qs or api_key_qs not in self._key_to_tenant:
                    await ws.close(code=4401, reason="AUTH_REQUIRED")
                    return

            await ws.accept()
            trace_id = ws.headers.get("X-Request-Id") or self._generate_trace_id()
            tenant_token = None
            gen = None  # ZZ-2: 流式 generator, finally 里 close 以中断后端 LLM (停止按钮)
            try:
                # 1. 收一条请求消息
                try:
                    body = await ws.receive_json()
                except Exception:
                    await ws.send_json({
                        "type": "error", "code": "BAD_REQUEST",
                        "message": "首条消息必须是 JSON object",
                        "trace_id": trace_id,
                    })
                    return

                if not isinstance(body, dict):
                    await ws.send_json({
                        "type": "error", "code": "BAD_REQUEST",
                        "message": "请求体必须是 JSON object",
                        "trace_id": trace_id,
                    })
                    return

                # 2. tenant reconcile — 复用 _resolve_tenant_from_auth 但走 ws.query_params
                auth_tid = self._key_to_tenant.get(api_key_qs) if api_key_qs else None
                body_tid = body.get("tenant_id")
                if auth_tid:
                    body["tenant_id"] = auth_tid
                # internal IP check: ws.client.host
                client_host = ws.client.host if ws.client else ""
                is_internal = any(
                    str(e).split("/")[0] and client_host.startswith(str(e).split("/")[0].rstrip("."))
                    for e in self.internal_whitelist
                )
                if not auth_tid and body_tid and not is_internal:
                    body.pop("tenant_id", None)

                effective_tid = str(body.get("tenant_id") or "default")
                if not self._is_known_tenant(effective_tid):
                    await ws.send_json({
                        "type": "error", "code": "TENANT_NOT_FOUND",
                        "message": "tenant 不存在或无权访问",
                        "trace_id": trace_id,
                    })
                    return

                # QPS quota
                if not self._check_qps_quota(effective_tid):
                    await ws.send_json({
                        "type": "error", "code": "API_RATE_LIMITED",
                        "message": "请求频率超出租户配额",
                        "trace_id": trace_id,
                    })
                    return

                tenant_token = set_current_tenant(effective_tid)

                # 3. 通知客户端"开始"
                await ws.send_json({
                    "type": "start",
                    "trace_id": trace_id,
                    "tenant_id": effective_tid,
                    "request_type": body.get("type", "unknown"),
                })

                # 4. 决定走"真实流式"还是"simulated 切片"
                #
                # 真实路径 (Task #44): RAG 模式 + handler 暴露 handle_stream
                #   -> 直接拉 generator, LLM TTFT 显著缩短
                #   -> 用户在 LLM 推理过程中就看到字, 不需要等整段
                #
                # simulated 路径: agent / hybrid / 老 adapter / handler 没 stream
                #   -> 先同步跑 handler.handle 拿完整结果, 再客户端体感切片
                start_t = time.time()
                loop = asyncio.get_event_loop()
                req_type = (body.get("type") or "rag").lower()
                # rag + agent 都支持真实流式 (Task #44 + #48); hybrid 留给后续
                can_stream = (
                    req_type in ("rag", "agent")
                    and hasattr(self.handler, "handle_stream")
                )

                # 仅 simulated 路径才需要先跑完 handler.handle
                result = None
                duration = 0.0
                if not can_stream:
                    result = await loop.run_in_executor(
                        None, lambda: self.handler.handle(body, trace_id=trace_id)
                    )
                    duration = time.time() - start_t
                    code = str(result.get("code", "UNKNOWN_ERROR"))
                    if code != "SUCCESS":
                        await ws.send_json({
                            "type": "error",
                            "code": code,
                            "message": result.get("message", ""),
                            "trace_id": trace_id,
                            "retryable": bool(result.get("retryable")),
                            "details": result.get("details"),
                        })
                        self._record_metrics(
                            req_type=req_type, code=code,
                            duration=duration, tenant_id=effective_tid,
                        )
                        return

                if can_stream:
                    # 真实流式: 直接从 handler.handle_stream 拉 event 转发
                    loop = asyncio.get_event_loop()
                    # handler.handle_stream 是 sync generator, 在 thread pool 跑
                    gen = self.handler.handle_stream(body, trace_id=trace_id)

                    def _next_or_stop(g):
                        try:
                            return ("event", next(g))
                        except StopIteration:
                            return ("end", None)
                        except Exception as e:
                            return ("err", e)

                    while True:
                        kind, val = await loop.run_in_executor(None, _next_or_stop, gen)
                        if kind == "end":
                            break
                        if kind == "err":
                            raise val  # 让外层 except 兜
                        evt = val
                        etype = evt.get("type")
                        if etype == "meta":
                            await ws.send_json({
                                "type": "metadata",
                                "citations": evt.get("citations") or [],
                                "retrieved_chunks": evt.get("retrieved_chunks") or [],
                                # Task #48: Agent stream 的 meta 含 steps + tool_results_summary
                                "steps": evt.get("steps") or [],
                                "tool_results_summary": evt.get("tool_results_summary") or [],
                            })
                        elif etype == "chunk":
                            await ws.send_json({"type": "chunk", "text": evt.get("text", "")})
                        elif etype in ("thought", "action", "observation"):
                            # Task #48: Agent ReAct 思维链事件直接透传给前端
                            await ws.send_json(evt)
                        elif etype == "done":
                            await ws.send_json({
                                "type": "done",
                                "code": "SUCCESS",
                                "cost_time": evt.get("cost_time", round(duration, 3)),
                                "trace_id": trace_id,
                            })
                        elif etype == "error":
                            await ws.send_json({
                                "type": "error",
                                "code": evt.get("code", "RAG_RUN_FAILED"),
                                "message": evt.get("message", ""),
                                "trace_id": trace_id,
                            })
                            return  # 错误后中止流
                    self._record_metrics(
                        req_type=req_type, code="SUCCESS", duration=duration,
                        tenant_id=effective_tid,
                    )
                else:
                    # ----- simulated 路径 (agent / hybrid / 老 adapter / mock LLM) -----
                    data = result.get("data") or {}
                    answer = str(data.get("answer") or "")
                    if answer:
                        base_interval_ms = 30
                        total_len = len(answer)
                        target_chunks = max(20, min(200, total_len // 3))
                        chunk_size = max(1, total_len // target_chunks)
                        interval = base_interval_ms / 1000.0
                    else:
                        chunk_size = 1
                        interval = 0.03
                    for i in range(0, len(answer), chunk_size):
                        await ws.send_json({"type": "chunk", "text": answer[i:i + chunk_size]})
                        await asyncio.sleep(interval)

                    await ws.send_json({
                        "type": "metadata",
                        "citations": data.get("citations") or [],
                        "retrieved_chunks": data.get("retrieved_chunks") or [],
                        "steps": data.get("steps") or [],
                    })

                    await ws.send_json({
                        "type": "done", "code": "SUCCESS",
                        "cost_time": round(duration, 3),
                        "trace_id": trace_id,
                    })

                    self._record_metrics(
                        req_type=req_type, code="SUCCESS", duration=duration,
                        tenant_id=effective_tid,
                    )
            except WebSocketDisconnect:
                # 客户端主动断开, 静默
                pass
            except Exception as e:
                self.logger.error(f"[ws] /invoke/stream 异常: {e}\n{traceback.format_exc()}")
                try:
                    await ws.send_json({
                        "type": "error", "code": "UNKNOWN_ERROR",
                        "message": str(e), "trace_id": trace_id,
                    })
                except Exception:
                    pass
            finally:
                # ZZ-2: 关闭流式 generator → 触发 chat_stream 链的 GeneratorExit →
                # requests response 连接关闭 → 中断后端 LLM 生成. 这样用户点停止 /
                # 客户端断开时, 后端不再继续读 token (不烧 token).
                if gen is not None:
                    try:
                        gen.close()
                    except Exception:
                        pass
                if tenant_token is not None:
                    reset_current_tenant(tenant_token)
                try:
                    await ws.close()
                except Exception:
                    pass
