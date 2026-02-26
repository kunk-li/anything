from __future__ import annotations

from functools import lru_cache

from ..core.service import FileManagerService
from ..storage.local import LocalStorage
from ..repository.memory import InMemoryRepository


@lru_cache
def get_repo() -> InMemoryRepository:
    # 生产换 SQL：这里替换即可
    return InMemoryRepository()


@lru_cache
def get_storage() -> LocalStorage:
    # 生产换 S3：这里替换即可
    return LocalStorage("./data")


def get_service() -> FileManagerService:
    return FileManagerService(storage=get_storage(), repo=get_repo())
