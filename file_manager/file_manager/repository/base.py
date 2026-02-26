from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, List, Tuple

from ..core.models import FileRecord, FileStatus


class FileRepository(ABC):
    """文件元数据仓储接口（可实现内存/SQL）"""

    @abstractmethod
    async def create(self, rec: FileRecord) -> FileRecord: ...

    @abstractmethod
    async def get(self, tenant_id: str, file_id: str) -> FileRecord: ...

    @abstractmethod
    async def update(self, rec: FileRecord) -> FileRecord: ...

    @abstractmethod
    async def soft_delete(self, tenant_id: str, file_id: str) -> None: ...

    @abstractmethod
    async def list(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
        status: Optional[FileStatus] = FileStatus.ACTIVE,
        q: Optional[str] = None,
    ) -> Tuple[List[FileRecord], Optional[str]]: ...
