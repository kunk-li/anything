from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..core.models import FileRecord, FileStatus
from ..core.exceptions import NotFound
from .base import FileRepository


class InMemoryRepository(FileRepository):
    """
    内存仓储：
    - 适合开发/单元测试
    - 多进程部署会丢数据（生产换 SQL）
    """

    def __init__(self) -> None:
        self._lock: asyncio.Lock = asyncio.Lock()
        # 主键：tenant_id:file_id
        self._files: Dict[str, FileRecord] = {}

    def _pk(self, tenant_id: str, file_id: str) -> str:
        """

        :param tenant_id:
        :param file_id:
        :return:
        """
        return f"{tenant_id}:{file_id}"

    async def create(self, rec: FileRecord) -> FileRecord:
        """
        创建
        :param rec:
        :return:
        """
        async with self._lock:
            self._files[self._pk(rec.tenant_id, rec.file_id)] = rec
        return rec

    async def get(self, tenant_id: str, file_id: str) -> FileRecord:
        """
        获取
        :param tenant_id:
        :param file_id:
        :return:
        """
        rec = self._files.get(self._pk(tenant_id, file_id))
        if rec is None or rec.status == FileStatus.DELETED:
            raise NotFound("文件不存在")
        return rec

    async def update(self, rec: FileRecord) -> FileRecord:
        """
        更新
        :param rec:
        :return:
        """
        rec.updated_at = datetime.utcnow()
        async with self._lock:
            self._files[self._pk(rec.tenant_id, rec.file_id)] = rec
        return rec

    async def soft_delete(self, tenant_id: str, file_id: str) -> None:
        """
        删除
        :param tenant_id:
        :param file_id:
        :return:
        """
        async with self._lock:
            pk = self._pk(tenant_id, file_id)
            rec = self._files.get(pk)
            if rec is None:
                # 幂等：不存在也不报错
                return
            rec.status = FileStatus.DELETED
            rec.deleted_at = datetime.utcnow()
            rec.updated_at = datetime.utcnow()
            self._files[pk] = rec

    async def list(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
        status: Optional[FileStatus] = FileStatus.ACTIVE,
        q: Optional[str] = None,
    ) -> Tuple[List[FileRecord], Optional[str]]:
        """
        简单列表：
        - cursor 这里用 created_at 的字符串或 file_id 都可以
        - 生产建议用 DB 分页
        :param tenant_id:
        :param limit:
        :param cursor:
        :param status:
        :param q:
        :return:
        """
        items: List[FileRecord] = [
            r for r in self._files.values()
            if r.tenant_id == tenant_id and (status is None or r.status == status)
        ]

        if q:
            q_lower = q.lower()
            items = [r for r in items if q_lower in r.filename.lower()]

        items.sort(key=lambda r: r.created_at, reverse=True)
        items = items[:limit]
        next_cursor: Optional[str] = None
        return items, next_cursor
