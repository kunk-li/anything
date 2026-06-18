# -*- coding: utf-8 -*-
"""
AdminRoutesMixin (Task LL #72)
GET /health         详细健康检查 (deps 检测)
GET /healthz        k8s liveness 探活
GET /metrics        Prometheus 文本
GET /admin/status   运行期状态总览 (Task S #53)
"""

from __future__ import annotations

from pathlib import Path  # /admin/status 用 Path(upload_dir); 缺它会 NameError 被吞→上传数永远空
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse


class AdminRoutesMixin:
    """管理 / 健康 / 指标路由."""

    def _register_admin_routes(self) -> None:
        @self.app.get("/health")
        async def health(request: Request):
            trace_id = request.state.trace_id
            data = {"status": "UP"}
            if self.enable_health_details:
                data["dependencies"] = {
                    "handler": "UP" if self.handler is not None else "DOWN"
                }

            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "ok",
                    "data": data,
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.get("/healthz")
        async def healthz(request: Request):
            trace_id = request.state.trace_id
            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "ok",
                    "data": {"status": "UP"},
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )

        @self.app.get("/metrics")
        async def metrics(request: Request):
            """Prometheus 文本格式 metrics 端点.

            可以直接被 Prometheus / VictoriaMetrics 抓取,无需中间件.
            示例 prometheus.yml scrape_config:
                - job_name: anything
                  static_configs:
                    - targets: ['localhost:8000']
            """
            return PlainTextResponse(
                content=self._render_prometheus_metrics(),
                status_code=200,
                headers={"X-Request-Id": request.state.trace_id},
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )
        # ============== Task S: 管理面板 — 运行期状态总览 ==============
        @self.app.get("/admin/status")
        async def admin_status(request: Request):
            """汇总当前运行期状态: hybrid 开关 / BM25 size / vector_db ntotal /
            注册模型数 / 上传文件计数. 给前端 admin 面板可视化用 (只读)。

            ⚠️ 生产部署在网关层屏蔽 /admin/* (除非已加 admin RBAC).
            """
            trace_id = request.state.trace_id
            data: Dict[str, Any] = {}

            # 1. RAG 配置 — hybrid 开关 / rerank / rewrite
            rag = self.rag_runner
            if rag is not None:
                data["rag"] = {
                    "enable_hybrid_search": bool(getattr(rag, "enable_hybrid_search", False)),
                    "enable_rerank": bool(getattr(rag, "enable_rerank", False)),
                    "enable_rewrite": bool(getattr(rag, "enable_rewrite", False)),
                    "top_k_retrieve": int(getattr(rag, "top_k_retrieve", 0)),
                    "top_k_rerank": int(getattr(rag, "top_k_rerank", 0)),
                    "history_max_turns": int(getattr(rag, "history_max_turns", 0)),
                    "hybrid_rrf_k": int(getattr(rag, "hybrid_rrf_k", 60)),
                }
                # BM25 size + avg_doc_len
                bm25 = getattr(rag, "bm25_retriever", None)
                if bm25 is not None:
                    data["bm25"] = {
                        "size": int(getattr(bm25, "size", 0)),
                        "avg_doc_len": round(float(getattr(bm25, "avg_doc_len", 0.0)), 1),
                    }
                else:
                    data["bm25"] = {"size": 0, "avg_doc_len": 0}
            else:
                data["rag"] = None
                data["bm25"] = None

            # 2. 向量库 ntotal
            if self.vector_db is not None:
                ntotal = None
                # FaissVectorDB 习惯把 ntotal 放在 .ntotal / .index.ntotal / 内部 index
                for attr in ("ntotal", "size"):
                    if hasattr(self.vector_db, attr):
                        val = getattr(self.vector_db, attr)
                        if callable(val):
                            try:
                                val = val()
                            except Exception:
                                val = None
                        if isinstance(val, int):
                            ntotal = val
                            break
                if ntotal is None and hasattr(self.vector_db, "index"):
                    idx = getattr(self.vector_db, "index", None)
                    if idx is not None and hasattr(idx, "ntotal"):
                        ntotal = int(idx.ntotal)
                data["vector_db"] = {"ntotal": ntotal if ntotal is not None else "unknown"}
            else:
                data["vector_db"] = None

            # 3. LLM 模型注册表概览
            if self.llm_service is not None:
                try:
                    models = self.llm_service.list_models(mask_keys=True) or []
                    data["llm_models"] = {
                        "count": len(models),
                        "by_type": {
                            t: sum(1 for m in models if m.get("request_type") == t)
                            for t in {m.get("request_type") for m in models if m.get("request_type")}
                        },
                    }
                except Exception:
                    data["llm_models"] = {"count": 0, "by_type": {}}
            else:
                data["llm_models"] = None

            # 4. 上传文件计数 (仅看 upload_dir 顶层文件数, 不递归)
            upload_dir = self.config.get_config("api_service.upload_dir", "./uploads")
            try:
                p = Path(upload_dir)
                if p.exists() and p.is_dir():
                    files = [x for x in p.iterdir() if x.is_file()]
                    data["uploads"] = {
                        "count": len(files),
                        "dir": str(p.resolve()),
                    }
                else:
                    data["uploads"] = {"count": 0, "dir": str(p.resolve()) if p else None}
            except Exception:
                data["uploads"] = None

            # 5. 安全 / 多租户开关
            data["security"] = {
                "auth_enabled": bool(self.auth_enabled),
                "auth_type": self.auth_type,
                "registered_tenants": sorted(set(self._key_to_tenant.values())) if self._key_to_tenant else [],
            }

            # 6. Task Y (#59): Token / cost 滚动汇总
            try:
                from observability_module import get_usage_tracker
                snap = get_usage_tracker().snapshot()
                # 截断 recent 列表节省体积 (admin 面板默认显示 top 10)
                snap["recent"] = (snap.get("recent") or [])[:10]
                data["usage"] = snap
            except Exception as e:
                self.logger.warning(f"[admin] 加载 usage tracker 失败: {e}")
                data["usage"] = None

            # 7. Task LLL (#98): Model health (HH/BBB) — 让前端模型表显状态
            try:
                from llm_adapter_module.utils import get_health_tracker
                data["health"] = get_health_tracker().snapshot()
            except Exception as e:
                self.logger.warning(f"[admin] 加载 health tracker 失败: {e}")
                data["health"] = None

            # 8. Task LLL (#98): Quota (BB/AAA) — 让前端显配额状态
            try:
                from quota_module import get_quota_guard
                data["quota"] = get_quota_guard().snapshot()
            except Exception as e:
                self.logger.warning(f"[admin] 加载 quota guard 失败: {e}")
                data["quota"] = None

            # 9. Task OOO (#101): Hooks 注册表 (Z)
            try:
                from hooks_module import get_hook_registry
                reg = get_hook_registry()
                data["hooks"] = {
                    "count": reg.count(),
                    "list": reg.list(),
                }
            except Exception as e:
                self.logger.warning(f"[admin] 加载 hook registry 失败: {e}")
                data["hooks"] = None

            # 10. Task OOO (#101): Skills 注册表 (AA)
            try:
                from skills_module import get_skill_registry
                sreg = get_skill_registry()
                skills = sreg.all_skills()
                data["skills"] = {
                    "loaded_from": getattr(sreg, "_loaded_from", None),
                    "count": len(skills),
                    "items": [
                        {
                            "name": s.name,
                            "description": s.description,
                            "tags": getattr(s, "tags", []) or [],
                            "content_preview": (getattr(s, "content", "") or "")[:200],
                        }
                        for s in skills[:50]  # 顶 50 条防 admin 面板被巨量 skills 撑爆
                    ],
                }
            except Exception as e:
                self.logger.warning(f"[admin] 加载 skill registry 失败: {e}")
                data["skills"] = None

            # 11. Task OOO (#101): Audit log 元数据 (CC) — 不读完整事件 (太大)
            try:
                from audit_module import get_audit_logger
                data["audit"] = get_audit_logger().snapshot()
            except Exception as e:
                self.logger.warning(f"[admin] 加载 audit logger 失败: {e}")
                data["audit"] = None

            # 12. Task PPP (#102): Scheduler (II) — 已注册 cron 任务列表
            if self.scheduler is not None:
                try:
                    data["scheduler"] = self.scheduler.snapshot()
                except Exception as e:
                    self.logger.warning(f"[admin] 加载 scheduler 失败: {e}")
                    data["scheduler"] = None
            else:
                data["scheduler"] = None

            return JSONResponse(
                status_code=200,
                content={
                    "code": "SUCCESS",
                    "message": "ok",
                    "data": data,
                    "trace_id": trace_id,
                    "retryable": False,
                    "details": None,
                },
                headers={"X-Request-Id": trace_id},
            )
