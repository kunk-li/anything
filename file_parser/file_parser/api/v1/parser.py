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





@router.post("/parse-and-chunk", response_model=FileParseResponse, description="文件解析+文本切片")
async def parse_and_chunk_file(request: FileParseRequest = Depends()):
    """
    接收文件信息，完成文件解析、文本提取、切片生成
    - 依赖文件管理模块提供的文件路径
    - 输出标准化切片列表，供后续向量化模块使用
    """
    return parser_service.parse_and_chunk_file(request)

@router.post("/chunk-only", response_model=List[Chunk], description="仅文本切片（无需重新解析文件）")
async def chunk_only(
    text: str = Depends(),
    file_id: UUID = Depends(),
    tenant_id: str = Depends(),
    chunk_size: int = None,
    chunk_overlap: int = None
):
    """仅对已解析的文本进行切片，适用于二次切片场景"""
    return chunker_service.create_chunks(text, file_id, tenant_id, chunk_size, chunk_overlap)