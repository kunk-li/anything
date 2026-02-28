from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict


class BaseExceptionHandler(ABC):
    """异常处理抽象基类（ABC）。

    所有异常处理实现类需继承此类，并实现：
    - handle_exception: 将异常封装为统一响应格式
    - get_exception_code: 根据异常获取系统错误码
    """

    @abstractmethod
    def handle_exception(self, exception: Exception) -> Dict[str, str]:
        """处理异常，封装异常信息。

        Args:
            exception: 捕获的异常对象（自定义异常或系统异常）

        Returns:
            dict: {"code": <错误码>, "message": <错误信息>}
        """
        raise NotImplementedError

    @abstractmethod
    def get_exception_code(self, exception: Exception) -> str:
        """获取异常编码，对应系统错误码表中的业务码。

        Args:
            exception: 异常对象

        Returns:
            str: 错误码（如 CONFIG_NOT_FOUND / UNKNOWN_ERROR）
        """
        raise NotImplementedError
