from __future__ import annotations

from fastapi import APIRouter
from .parser import router as module_router

router = APIRouter()
router.include_router(module_router, prefix="/file_parser", tags=["file_parser"])
