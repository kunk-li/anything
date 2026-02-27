from __future__ import annotations

from fastapi import APIRouter
from .module__name import router as module_router

router = APIRouter()
router.include_router(module_router, prefix="/module__name", tags=["module__name"])
