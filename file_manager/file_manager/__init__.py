# 导出核心对象，方便外部系统直接 import 使用
from .core.service import FileManagerService
from .storage.local import LocalStorage
from .repository.memory import InMemoryRepository

__all__ = ["FileManagerService", "LocalStorage", "InMemoryRepository"]
