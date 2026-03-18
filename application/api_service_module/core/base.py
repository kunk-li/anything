"""API服务模块抽象接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from fastapi import FastAPI


class BaseApiService(ABC):
    """API 服务模块抽象基类。"""

    @abstractmethod
    def create_app(self) -> FastAPI:
        """创建并返回 FastAPI 应用。"""

    @abstractmethod
    def set_handler(self, handler: Any) -> None:
        """注入请求处理器。"""

    @abstractmethod
    def set_index_builder(self, builder: Optional[Any]) -> None:
        """注入索引构建器。"""

    @abstractmethod
    def health_snapshot(self) -> Dict[str, Any]:
        """返回当前健康状态。"""
