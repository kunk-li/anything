from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, AsyncIterator, Tuple


class FileStatus(str, Enum):
    """文件状态枚举"""
    PENDING = "PENDING"   # 已创建但未完成（大文件/会话式上传会用到）
    ACTIVE = "ACTIVE"     # 可用
    DELETED = "DELETED"   # 软删除
    FAILED = "FAILED"     # 失败


@dataclass(frozen=True)
class ByteRange:
    """字节范围：start-end（end 为包含端点；None 表示直到末尾）"""
    start: int
    end: Optional[int] = None


@dataclass(frozen=True)
class ObjectMeta:
    """Storage 层返回的对象元信息"""
    key: str
    size: int
    etag: Optional[str] = None
    content_type: Optional[str] = None


@dataclass
class FileRecord:
    """文件元数据（存储在 repository 层）"""
    file_id: str
    tenant_id: str
    storage_key: str

    filename: str
    content_type: Optional[str]
    size: int

    sha256: Optional[str] = None
    etag: Optional[str] = None
    status: FileStatus = FileStatus.ACTIVE
    metadata: Dict[str, Any] = field(default_factory=dict)

    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = None
