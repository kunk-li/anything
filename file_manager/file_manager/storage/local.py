from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import AsyncIterator, Optional, Dict, Tuple



from ..core.models import ObjectMeta, ByteRange
from .base import Storage, StorageNotFound

_CHUNK_SIZE: int = 1024 * 1024  # 1MB


class LocalStorage(Storage):
    """
    本地文件系统实现：
    - 适合开发/小规模
    - 生产建议换成对象存储（S3/MinIO）
    """

    def __init__(self, root_dir: str) -> None:
        self.root: Path = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for_key(self, key: str) -> Path:
        """
        将 storage key 映射为本地路径，并做基本安全校验（防路径穿越）
        """
        p = Path(key)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError("非法 key：禁止绝对路径或 ..")
        return self.root / p

    async def put(
        self,
        key: str,
        body: AsyncIterator[bytes],
        *,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        content_length: Optional[int] = None,
    ) -> ObjectMeta:
        path = self._path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        size: int = 0
        md5 = hashlib.md5()
        import aiofiles
        async with aiofiles.open(path, "wb") as f:
            async for chunk in body:
                if not chunk:
                    continue
                size += len(chunk)
                md5.update(chunk)
                await f.write(chunk)

        return ObjectMeta(key=key, size=size, etag=md5.hexdigest(), content_type=content_type)

    async def head(self, key: str) -> ObjectMeta:
        path = self._path_for_key(key)
        if not path.exists():
            raise StorageNotFound(key)
        st = path.stat()
        return ObjectMeta(key=key, size=st.st_size)

    async def get(
        self,
        key: str,
        *,
        byte_range: Optional[ByteRange] = None,
    ) -> Tuple[AsyncIterator[bytes], ObjectMeta]:
        path = self._path_for_key(key)
        if not path.exists():
            raise StorageNotFound(key)

        st = path.stat()
        meta = ObjectMeta(key=key, size=st.st_size)

        start: int = 0
        end_inclusive: Optional[int] = None

        if byte_range is not None:
            start = max(0, byte_range.start)
            if byte_range.end is not None:
                end_inclusive = min(byte_range.end, meta.size - 1)

        async def streamer() -> AsyncIterator[bytes]:
            import aiofiles
            async with aiofiles.open(path, "rb") as f:
                await f.seek(start)

                remaining: Optional[int] = None
                if end_inclusive is not None:
                    remaining = (end_inclusive - start) + 1

                while True:
                    if remaining is not None and remaining <= 0:
                        break
                    read_size = _CHUNK_SIZE if remaining is None else min(_CHUNK_SIZE, remaining)
                    data = await f.read(read_size)
                    if not data:
                        break
                    if remaining is not None:
                        remaining -= len(data)
                    yield data

        return streamer(), meta

    async def delete(self, key: str) -> None:
        path = self._path_for_key(key)
        try:
            os.remove(path)
        except FileNotFoundError:
            # 幂等：不存在也视为成功
            return
