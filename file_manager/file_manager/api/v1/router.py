from __future__ import annotations

from fastapi import APIRouter
from .files import router as files_router

router = APIRouter()
router.include_router(files_router, prefix="/files", tags=["files"])
