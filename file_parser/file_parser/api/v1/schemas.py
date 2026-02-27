from __future__ import annotations

from pydantic import BaseModel
from typing import Optional, Dict, Any, List


class ErrorOut(BaseModel):
    error: dict
