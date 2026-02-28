"""抽象基类（ABC）定义。

说明：本文件只定义接口规范，不包含业务实现逻辑。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Union, Optional, Tuple
from datetime import datetime


class BaseTextTool(ABC):
    """文本处理抽象基类，定义所有文本相关工具的核心接口。"""

    @abstractmethod
    def text_clean(self, text: str) -> str:
        """文本基础清洗。"""
        raise NotImplementedError

    @abstractmethod
    def text_desensitize(self, text: str, type_: str) -> str:
        """文本脱敏。"""
        raise NotImplementedError

    @abstractmethod
    def text_validate(self, text: str, type_: str) -> bool:
        """文本格式校验。"""
        raise NotImplementedError


class BaseDataTool(ABC):
    """数据处理抽象基类，定义数据转换、处理相关工具的核心接口。"""

    @abstractmethod
    def dict_to_json(self, data: Dict) -> str:
        """字典转JSON字符串。"""
        raise NotImplementedError

    @abstractmethod
    def data_convert(self, data: Any, target_type: str) -> Any:
        """通用数据类型转换。"""
        raise NotImplementedError

    @abstractmethod
    def list_deduplicate(self, data_list: List) -> List:
        """列表去重（保留原顺序）。"""
        raise NotImplementedError


class BaseParamValidate(ABC):
    """参数校验抽象基类，定义参数校验相关的核心接口。"""

    @abstractmethod
    def required_validate(self, params: Dict, required_params: List[str]) -> bool:
        """必填参数校验（支持嵌套 key1.key2）。"""
        raise NotImplementedError

    @abstractmethod
    def range_validate(
        self,
        value: Union[int, float, str],
        min_val: Union[int, float, int],
        max_val: Union[int, float, int],
    ) -> bool:
        """范围校验（数值范围 / 字符串长度范围）。"""
        raise NotImplementedError


class BaseAssistTool(ABC):
    """通用辅助工具抽象基类（重点包含时间相关接口）。"""

    @abstractmethod
    def md5_encrypt(self, text: str) -> str:
        """MD5加密。"""
        raise NotImplementedError

    # 时间相关接口（重点扩张）
    @abstractmethod
    def get_current_time(self, format_: str = "YYYY-MM-DD HH:MM:SS", time_zone: Optional[str] = None) -> str:
        """获取当前时间字符串。"""
        raise NotImplementedError

    @abstractmethod
    def get_timestamp(
        self,
        time_str: Optional[str] = None,
        format_: str = "YYYY-MM-DD HH:MM:SS",
        millisecond: bool = False,
        time_zone: Optional[str] = None,
    ) -> int:
        """获取时间戳（秒/毫秒）。"""
        raise NotImplementedError

    @abstractmethod
    def time_convert(
        self,
        time_data: Union[str, datetime, int],
        target_type: str,
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> Union[str, datetime, int]:
        """时间类型转换（str / datetime / timestamp）。"""
        raise NotImplementedError

    @abstractmethod
    def time_diff_calculate(
        self,
        start_time: Union[str, datetime, int],
        end_time: Union[str, datetime, int],
        unit: str = "day",
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> float:
        """计算两个时间的差值。"""
        raise NotImplementedError

    @abstractmethod
    def time_offset(
        self,
        time_data: Union[str, datetime, int],
        offset: int,
        unit: str = "day",
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> str:
        """计算时间偏移后的时间字符串。"""
        raise NotImplementedError

    @abstractmethod
    def is_in_time_range(
        self,
        time_data: Union[str, datetime, int],
        start_range: Union[str, datetime, int],
        end_range: Union[str, datetime, int],
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> bool:
        """判断时间是否在区间内（闭区间）。"""
        raise NotImplementedError

    @abstractmethod
    def get_time_segment(
        self,
        time_data: Union[str, datetime, int],
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> str:
        """获取时段（凌晨/上午/下午/晚上）。"""
        raise NotImplementedError

    # 扩展时间方法（文档示例中提到）
    @abstractmethod
    def get_day_start_end(
        self,
        time_data: Optional[Union[str, datetime, int]] = None,
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> Tuple[str, str]:
        """获取指定日期当天起止时间（00:00:00 ~ 23:59:59）。"""
        raise NotImplementedError

    @abstractmethod
    def get_month_start_end(
        self,
        time_data: Optional[Union[str, datetime, int]] = None,
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> Tuple[str, str]:
        """获取指定日期当月起止时间。"""
        raise NotImplementedError

    @abstractmethod
    def get_year_start_end(
        self,
        time_data: Optional[Union[str, datetime, int]] = None,
        format_: str = "YYYY-MM-DD HH:MM:SS",
        time_zone: Optional[str] = None,
    ) -> Tuple[str, str]:
        """获取指定日期当年起止时间。"""
        raise NotImplementedError


class BaseUtils(ABC):
    """通用工具总抽象基类，整合所有工具类接口。"""

    @abstractmethod
    def get_text_tool(self) -> BaseTextTool:
        raise NotImplementedError

    @abstractmethod
    def get_data_tool(self) -> BaseDataTool:
        raise NotImplementedError

    @abstractmethod
    def get_param_validate(self) -> BaseParamValidate:
        raise NotImplementedError

    @abstractmethod
    def get_assist_tool(self) -> BaseAssistTool:
        raise NotImplementedError
