from __future__ import annotations

import uuid
from datetime import datetime
from typing import AsyncIterator, Optional, Tuple, List, Dict, Any

from .models import FileRecord, FileStatus, ByteRange, ObjectMeta
from .exceptions import NotFound, RangeNotSatisfiable
from .keygen import make_storage_key
from ..repository.base import FileRepository
from ..storage.base import Storage, StorageNotFound


class FileManagerService:
    """
    核心服务（纯业务）：
    - 不依赖 FastAPI
    - 既可被其他系统当库调用，也可被 api 层调用
    """

    def __init__(self, *, storage: Storage, repo: FileRepository) -> None:
        """

        :param storage:
        :param repo:
        """
        self.storage = storage
        self.repo = repo

    async def upload(
        self,
        *,
        tenant_id: str,
        filename: str,
        content_type: Optional[str],
        body: AsyncIterator[bytes],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FileRecord:
        """
        上传文件（流式）：
        1) 生成 storage_key
        2) 写入 storage
        3) 写入 repo 元数据
        :param tenant_id:
        :param filename:
        :param content_type:
        :param body:
        :param metadata:
        :return:
        """
        file_id: str = f"f_{uuid.uuid4().hex}"
        storage_key: str = make_storage_key(tenant_id, filename)

        obj_meta: ObjectMeta = await self.storage.put(
            storage_key, body, content_type=content_type
        )

        rec = FileRecord(
            file_id=file_id,
            tenant_id=tenant_id,
            storage_key=obj_meta.key,
            filename=filename,
            content_type=content_type,
            size=obj_meta.size,
            etag=obj_meta.etag,
            status=FileStatus.ACTIVE,
            metadata=metadata or {},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        return await self.repo.create(rec)

    async def info(self, *, tenant_id: str, file_id: str) -> FileRecord:
        """
        读取文件元信息
        :param tenant_id:
        :param file_id:
        :return:
        """
        return await self.repo.get(tenant_id, file_id)

    async def download(
        self,
        *,
        tenant_id: str,
        file_id: str,
        byte_range: Optional[ByteRange] = None,
    ) -> Tuple[AsyncIterator[bytes], FileRecord, ObjectMeta, Optional[Tuple[int, int]]]:
        """
        下载文件：
        返回：
        - stream：字节流
        - rec：文件记录
        - meta：对象元信息
        - ranged：如果是 Range 请求，返回 (start, end_inclusive)；否则 None
        :param tenant_id:
        :param file_id:
        :param byte_range:
        :return:
        """
        rec: FileRecord = await self.repo.get(tenant_id, file_id)

        try:
            stream, meta = await self.storage.get(rec.storage_key, byte_range=byte_range)
        except StorageNotFound:
            raise NotFound("文件内容不存在")

        # 如果请求 Range，需要在这里校验范围是否可满足
        if byte_range is not None:
            start = max(0, byte_range.start)
            if start >= meta.size:
                raise RangeNotSatisfiable("Range 超出文件大小")
            end = (meta.size - 1) if byte_range.end is None else min(byte_range.end, meta.size - 1)
            return stream, rec, meta, (start, end)

        return stream, rec, meta, None

    async def delete(self, *, tenant_id: str, file_id: str) -> None:
        """
        删除文件（幂等）：
        - repo 软删
        - storage 物理删（后期可改为异步）
        :param tenant_id:
        :param file_id:
        :return:
        """
        try:
            rec = await self.repo.get(tenant_id, file_id)
        except NotFound:
            # 幂等：不存在也算成功
            return

        await self.repo.soft_delete(tenant_id, file_id)
        await self.storage.delete(rec.storage_key)

    async def list_files(
        self,
        *,
        tenant_id: str,
        limit: int = 50,
        cursor: Optional[str] = None,
        q: Optional[str] = None,
    ) -> Tuple[List[FileRecord], Optional[str]]:
        """
        文件列表（简单分页接口，cursor 可后续扩展）
        :param tenant_id:
        :param limit:
        :param cursor:
        :param q:
        :return:
        """
        return await self.repo.list(tenant_id, limit=limit, cursor=cursor, q=q)
