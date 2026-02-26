from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional, Dict, Tuple

from ..core.models import ObjectMeta, ByteRange


class StorageError(Exception):
    """存储层异常基类"""


class StorageNotFound(StorageError):
    """存储对象不存在"""


class Storage(ABC):
    """
    存储层抽象：
    - 上层（core/service）只依赖本接口
    - 可实现：本地、S3/MinIO、OSS 等
    """

    @abstractmethod
    async def put(
        self,
        key: str,
        body: AsyncIterator[bytes],
        *,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        content_length: Optional[int] = None,
    ) -> ObjectMeta:
        """流式上传对象，返回元信息"""
        raise NotImplementedError

    @abstractmethod
    async def get(
        self,
        key: str,
        *,
        byte_range: Optional[ByteRange] = None,
    ) -> Tuple[AsyncIterator[bytes], ObjectMeta]:
        """流式下载对象（支持 Range）"""
        raise NotImplementedError

    @abstractmethod
    async def head(self, key: str) -> ObjectMeta:
        """读取对象元信息"""
        raise NotImplementedError

    @abstractmethod
    async def delete(self, key: str) -> None:
        """删除对象（建议幂等：不存在也返回成功）"""
        raise NotImplementedError
