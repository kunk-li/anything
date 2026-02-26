from __future__ import annotations

from fastapi import FastAPI
from .api.v1.router import router as v1_router


def create_app() -> FastAPI:
    """
    工厂函数：
    - 外部系统可直接 import 并挂载
    - uvicorn --factory 可以启动它
    """
    app = FastAPI(title="FileManager")
    app.include_router(v1_router, prefix="/api/v1")

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    return app
