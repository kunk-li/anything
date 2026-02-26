# file_manager/repository/sql.py
from __future__ import annotations

"""
基于 SQLAlchemy asyncio 的 FileRepository 实现（PostgreSQL via asyncpg）
- 提供 create/get/update/soft_delete/list 接口
- 需要在项目中提供 async engine 与 sessionmaker（示例中我们用简单的 create_engine）
"""

from typing import Optional, List, Tuple
from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    Text,
    JSON,
    Enum as SAEnum,
    select,
    update,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base, mapped_column
import enum

from ..core.models import FileRecord, FileStatus
from ..core.exceptions import NotFound
from .base import FileRepository

Base = declarative_base()


class FileStatusEnum(str, enum.Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"
    FAILED = "FAILED"


class FileOrm(Base):
    __tablename__ = "files"

    file_id = Column(String(64), primary_key=True, index=True)
    tenant_id = Column(String(64), primary_key=False, index=True)
    storage_key = Column(String(512), unique=True, nullable=False)
    filename = Column(String(512), nullable=False)
    content_type = Column(String(128), nullable=True)
    size = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(128), nullable=True)
    etag = Column(String(128), nullable=True)
    status = Column(SAEnum(FileStatusEnum), nullable=False, default=FileStatusEnum.ACTIVE)
    metadata = Column(JSON, nullable=True, default={})
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


def orm_to_record(row: FileOrm) -> FileRecord:
    return FileRecord(
        file_id=row.file_id,
        tenant_id=row.tenant_id,
        storage_key=row.storage_key,
        filename=row.filename,
        content_type=row.content_type,
        size=row.size,
        sha256=row.sha256,
        etag=row.etag,
        status=FileStatus(row.status.value) if isinstance(row.status, FileStatusEnum) else FileStatus(row.status),
        metadata=row.metadata or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


class SQLFileRepository(FileRepository):
    """
    SQL 实现需要传入 async_session_maker：
        engine = create_async_engine("postgresql+asyncpg://user:pass@host/db")
        SessionMaker = async_sessionmaker(engine, expire_on_commit=False)
        repo = SQLFileRepository(SessionMaker)
    """

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = session_maker

    async def create(self, rec: FileRecord) -> FileRecord:
        async with self._sm() as session:
            orm = FileOrm(
                file_id=rec.file_id,
                tenant_id=rec.tenant_id,
                storage_key=rec.storage_key,
                filename=rec.filename,
                content_type=rec.content_type,
                size=rec.size,
                sha256=rec.sha256,
                etag=rec.etag,
                status=FileStatusEnum(rec.status.value),
                metadata=rec.metadata,
                created_at=rec.created_at,
                updated_at=rec.updated_at,
                deleted_at=rec.deleted_at,
            )
            session.add(orm)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            # refresh to get defaults if any
            await session.refresh(orm)
            return orm_to_record(orm)

    async def get(self, tenant_id: str, file_id: str) -> FileRecord:
        async with self._sm() as session:
            stmt = select(FileOrm).where(FileOrm.file_id == file_id, FileOrm.tenant_id == tenant_id)
            res = await session.execute(stmt)
            row = res.scalar_one_or_none()
            if row is None or row.status == FileStatusEnum.DELETED:
                raise NotFound("文件不存在")
            return orm_to_record(row)

    async def update(self, rec: FileRecord) -> FileRecord:
        async with self._sm() as session:
            stmt = (
                update(FileOrm)
                .where(FileOrm.file_id == rec.file_id, FileOrm.tenant_id == rec.tenant_id)
                .values(
                    storage_key=rec.storage_key,
                    filename=rec.filename,
                    content_type=rec.content_type,
                    size=rec.size,
                    sha256=rec.sha256,
                    etag=rec.etag,
                    status=FileStatusEnum(rec.status.value),
                    metadata=rec.metadata,
                    updated_at=rec.updated_at,
                    deleted_at=rec.deleted_at,
                )
            )
            await session.execute(stmt)
            await session.commit()
            # 返回最新记录
            return await self.get(rec.tenant_id, rec.file_id)

    async def soft_delete(self, tenant_id: str, file_id: str) -> None:
        async with self._sm() as session:
            stmt = (
                update(FileOrm)
                .where(FileOrm.file_id == file_id, FileOrm.tenant_id == tenant_id)
                .values(status=FileStatusEnum.DELETED, deleted_at=datetime.utcnow())
            )
            await session.execute(stmt)
            await session.commit()
            return

    async def list(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
        status: Optional[FileStatus] = FileStatus.ACTIVE,
        q: Optional[str] = None,
    ) -> Tuple[List[FileRecord], Optional[str]]:
        async with self._sm() as session:
            stmt = select(FileOrm).where(FileOrm.tenant_id == tenant_id)
            if status is not None:
                stmt = stmt.where(FileOrm.status == FileStatusEnum(status.value))
            if q:
                # 简单的 filename like 查询；生产请用全文/搜索索引
                stmt = stmt.where(FileOrm.filename.ilike(f"%{q}%"))
            stmt = stmt.order_by(FileOrm.created_at.desc()).limit(limit)
            res = await session.execute(stmt)
            rows = res.scalars().all()
            items = [orm_to_record(r) for r in rows]
            next_cursor = None
            return items, next_cursor
