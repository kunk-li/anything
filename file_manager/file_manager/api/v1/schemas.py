from __future__ import annotations

from pydantic import BaseModel
from typing import Optional, Dict, Any, List


class FileOut(BaseModel):
    file_id: str
    filename: str
    content_type: Optional[str] = None
    size: int
    status: str
    metadata: Dict[str, Any] = {}


class FileListOut(BaseModel):
    items: List[FileOut]
    next_cursor: Optional[str] = None


class ErrorOut(BaseModel):
    error: dict
