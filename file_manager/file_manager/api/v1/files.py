from __future__ import annotations

from typing import Optional, AsyncIterator, Dict, Any

from fastapi import APIRouter, Depends, UploadFile, File, Header, HTTPException
from fastapi.responses import StreamingResponse, Response

from ..deps import get_service
from .schemas import FileOut, FileListOut
from ...core.service import FileManagerService
from ...core.exceptions import NotFound, RangeNotSatisfiable
from ...utils.range import parse_range_header
from ...utils.http import build_content_disposition  # ✅ 新增：兼容中文文件名

router = APIRouter()


def _tenant_from_header(x_tenant_id: Optional[str]) -> str:
    """
    从请求头获取 tenant_id：
    - 为了后续接入 RAG/Agent 多租户，建议强制要求
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="缺少 X-Tenant-Id")
    return x_tenant_id


async def iter_uploadfile(file: UploadFile, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    """
    将 UploadFile 以异步迭代方式流式读取，避免一次性读入内存（支持大文件上传）
    """
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        yield chunk


@router.post("", response_model=FileOut)
async def upload(
    file: UploadFile = File(...),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    service: FileManagerService = Depends(get_service),
) -> FileOut:
    tenant_id: str = _tenant_from_header(x_tenant_id)

    rec = await service.upload(
        tenant_id=tenant_id,
        filename=file.filename or "unnamed",
        content_type=file.content_type,
        body=iter_uploadfile(file),
        metadata={},  # 你后续可以从额外表单字段/JSON 传入
    )

    return FileOut(
        file_id=rec.file_id,
        filename=rec.filename,
        content_type=rec.content_type,
        size=rec.size,
        status=rec.status.value,
        metadata=rec.metadata,
    )


@router.get("/{file_id}", response_model=FileOut)
async def info(
    file_id: str,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    service: FileManagerService = Depends(get_service),
) -> FileOut:
    tenant_id: str = _tenant_from_header(x_tenant_id)

    try:
        rec = await service.info(tenant_id=tenant_id, file_id=file_id)
    except NotFound:
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileOut(
        file_id=rec.file_id,
        filename=rec.filename,
        content_type=rec.content_type,
        size=rec.size,
        status=rec.status.value,
        metadata=rec.metadata,
    )


@router.get("", response_model=FileListOut)
async def list_files(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    limit: int = 50,
    q: Optional[str] = None,
    service: FileManagerService = Depends(get_service),
) -> FileListOut:
    tenant_id: str = _tenant_from_header(x_tenant_id)

    items, next_cursor = await service.list_files(tenant_id=tenant_id, limit=limit, q=q)

    return FileListOut(
        items=[
            FileOut(
                file_id=r.file_id,
                filename=r.filename,
                content_type=r.content_type,
                size=r.size,
                status=r.status.value,
                metadata=r.metadata,
            )
            for r in items
        ],
        next_cursor=next_cursor,
    )


@router.delete("/{file_id}")
async def delete(
    file_id: str,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    service: FileManagerService = Depends(get_service),
) -> Dict[str, Any]:
    tenant_id: str = _tenant_from_header(x_tenant_id)
    await service.delete(tenant_id=tenant_id, file_id=file_id)
    return {"ok": True, "file_id": file_id}


@router.get("/{file_id}/content")
async def download(
    file_id: str,
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    range_header: Optional[str] = Header(default=None, alias="Range"),
    service: FileManagerService = Depends(get_service),
):
    tenant_id: str = _tenant_from_header(x_tenant_id)

    # 解析 Range 头（如果有）
    byte_range = None
    if range_header:
        byte_range = parse_range_header(range_header)
        if byte_range is None:
            raise HTTPException(status_code=400, detail="Range 格式不合法")

    # ✅ 先尝试拿到文件信息（用于 416 时返回 total size）
    try:
        rec = await service.info(tenant_id=tenant_id, file_id=file_id)
    except NotFound:
        raise HTTPException(status_code=404, detail="文件不存在")

    # ✅ 再走下载（可能抛 416）
    try:
        stream, rec, meta, ranged = await service.download(
            tenant_id=tenant_id, file_id=file_id, byte_range=byte_range
        )
    except RangeNotSatisfiable:
        # 416 必须带 Content-Range: bytes */total
        # 这里 meta 不一定存在，所以用 storage.head 获取 size 更稳
        try:
            meta = await service.storage.head(rec.storage_key)
            total_size = meta.size
        except Exception:
            total_size = rec.size  # 兜底（至少能返回一个数字）
        return Response(status_code=416, headers={"Content-Range": f"bytes */{total_size}"})
    except NotFound:
        raise HTTPException(status_code=404, detail="文件内容不存在")

    headers = {
        "Accept-Ranges": "bytes",
        # ✅ 修复点：Content-Disposition 需要兼容中文文件名（RFC5987）
        "Content-Disposition": build_content_disposition(rec.filename),
    }

    # Range 响应：206
    if ranged is not None:
        start, end = ranged
        headers["Content-Range"] = f"bytes {start}-{end}/{meta.size}"
        headers["Content-Length"] = str((end - start) + 1)
        return StreamingResponse(
            stream,
            status_code=206,
            media_type=rec.content_type or "application/octet-stream",
            headers=headers,
        )

    # 普通下载：200
    headers["Content-Length"] = str(meta.size)
    return StreamingResponse(
        stream,
        media_type=rec.content_type or "application/octet-stream",
        headers=headers,
    )
