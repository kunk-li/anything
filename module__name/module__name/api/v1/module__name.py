from __future__ import annotations

from typing import Optional, AsyncIterator, Dict, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, Response

router = APIRouter()


def _tenant_from_header(x_tenant_id: Optional[str]) -> str:
    """
    从请求头获取 tenant_id：
    - 为了后续接入 RAG/Agent 多租户，建议强制要求
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="缺少 X-Tenant-Id")
    return x_tenant_id
