from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class BaseStateStore(ABC):
    """状态存储抽象基类，定义Agent状态存储核心接口，所有具体实现类需继承此类并实现所有抽象方法。"""

    @abstractmethod
    def save_state(self, session_id: str, state: Dict[str, Any]) -> bool:
        """保存指定会话的Agent状态，若会话已存在则覆盖原有状态。"""
        raise NotImplementedError

    @abstractmethod
    def get_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        """读取指定会话的Agent状态，若会话不存在或读取失败则返回None。"""
        raise NotImplementedError

    @abstractmethod
    def append_event(self, session_id: str, event: Dict[str, Any]) -> bool:
        """向指定会话的状态中追加事件记录，用于跟踪Agent任务执行过程。"""
        raise NotImplementedError

    @abstractmethod
    def clear_state(self, session_id: str) -> bool:
        """清理指定会话的状态数据，释放存储资源。"""
        raise NotImplementedError
