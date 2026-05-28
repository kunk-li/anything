# -*- coding: utf-8 -*-
"""
FrontendRoutesMixin (Task LL #72)
GET /          → frontend/index.html (no-cache)
GET /ui        → 别名
/static/*      → frontend/static/ StaticFiles 挂载

⚠️ 这些路由在 _register_routes 最后注册, 避免覆盖 /admin/status 等 GET 端点.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


class FrontendRoutesMixin:
    """前端 SPA 入口 + 静态资源挂载."""

    def _register_frontend_routes(self) -> None:
        """挂载 frontend/ 静态资源 + GET / 返回 index.html.

        路径解析: <api_service_module/core/routers/frontend.py>
            -> parents[4] = repo root → /frontend
            -> or <cwd>/frontend/         (兜底)
            -> or <cwd>/../frontend/      (在 run/ 下启动时)
        找不到 index.html 时跳过挂载并 INFO 日志, 不挂掉服务.
        """
        candidates = [
            Path(__file__).resolve().parents[4] / "frontend",
            Path.cwd() / "frontend",
            Path.cwd().parent / "frontend",
        ]
        frontend_dir = next((p for p in candidates if (p / "index.html").exists()), None)
        if frontend_dir is None:
            self.logger.info(
                "未找到 frontend/index.html, 跳过 Web UI 挂载 (纯 API 模式)"
            )
            return

        static_dir = frontend_dir / "static"
        if static_dir.is_dir():
            self.app.mount(
                "/static", StaticFiles(directory=str(static_dir)), name="static"
            )

        index_path = frontend_dir / "index.html"

        # 主页 HTML 加 no-cache header, 避免浏览器缓存老 UI.
        _NO_CACHE_HEADERS = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }

        @self.app.get("/")
        async def _root():
            return FileResponse(
                str(index_path),
                media_type="text/html; charset=utf-8",
                headers=_NO_CACHE_HEADERS,
            )

        @self.app.get("/ui")
        async def _ui():
            return FileResponse(
                str(index_path),
                media_type="text/html; charset=utf-8",
                headers=_NO_CACHE_HEADERS,
            )

        self.logger.info(
            f"Web UI 已挂载: GET / 提供 index.html, 静态资源 from {static_dir}"
        )
