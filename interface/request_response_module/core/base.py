# -*- coding: utf-8 -*-
"""
请求响应处理模块抽象基类
定义模块核心接口，强制所有实现类必须实现，保障模块一致性、可替换性
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple

from ..model.data_model import UnifiedRequest, UnifiedResponse


class BaseRequestHandler(ABC):
    """请求响应处理模块抽象基类，所有处理实现类必须继承此类"""

    @abstractmethod
    def validate_request(self, request: Dict[str, Any]) -> Tuple[bool, str]:
        """
        请求参数校验：校验请求字段完整性、类型、格式
        :param request: 原始请求字典
        :return: 元组（校验是否通过，错误信息）
        """
        pass

    @abstractmethod
    def handle(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理请求：完成参数校验、调用调度模块、封装响应
        :param request: 原始请求字典
        :return: 标准化响应字典（与 UnifiedResponse 结构一致）
        :raises Exception: 处理失败时抛出异常
        """
        pass

    @abstractmethod
    def format_response(self, code: str, message: str, data: Any,
                        trace_id: str, cost_time: float = None) -> Dict[str, Any]:
        """
        格式化响应：将处理结果封装为系统统一响应格式
        :param code: 响应码
        :param message: 响应信息
        :param  响应数据
        :param trace_id: 链路追踪 ID
        :param cost_time: 调用耗时
        :return: 标准化响应字典
        """
        pass

    @abstractmethod
    def handle_exception(self, exception: Exception, trace_id: str) -> Dict[str, Any]:
        """
        异常处理：捕获异常并转换为标准化错误响应
        :param exception: 捕获的异常对象
        :param trace_id: 链路追踪 ID
        :return: 标准化错误响应字典
        """
        pass