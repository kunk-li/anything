"""数据处理工具类实现。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List

from ..core.base import BaseDataTool
from .assist_tool import _normalize_format, _parse_time_str


class DataTool(BaseDataTool):
    """数据处理工具类。"""

    def dict_to_json(self, data: Dict) -> str:
        if not isinstance(data, dict):
            raise TypeError("data 必须为 dict 类型")
        return json.dumps(data, ensure_ascii=False)

    def data_convert(self, data: Any, target_type: str) -> Any:
        if target_type is None or not isinstance(target_type, str) or not target_type.strip():
            raise ValueError("target_type 不能为空")
        t = target_type.strip().lower()

        if t == "str":
            return "" if data is None else str(data)
        if t == "int":
            if data is None or data == "":
                raise ValueError("无法将空值转换为 int")
            return int(data)
        if t == "bool":
            if isinstance(data, bool):
                return data
            if isinstance(data, (int, float)):
                return bool(data)
            if isinstance(data, str):
                s = data.strip().lower()
                if s in {"true", "1", "yes", "y"}:
                    return True
                if s in {"false", "0", "no", "n"}:
                    return False
            raise ValueError('无法转换为 bool，支持 "true/false/1/0/yes/no" 等')
        if t == "datetime":
            if isinstance(data, datetime):
                return data
            if isinstance(data, int):
                # 默认秒级时间戳
                return datetime.fromtimestamp(data)
            if isinstance(data, str):
                # 默认使用 YYYY-MM-DD HH:MM:SS
                return _parse_time_str(data, "YYYY-MM-DD HH:MM:SS", None)
            raise TypeError("无法转换为 datetime，支持 str/int/datetime")
        raise ValueError('target_type 不支持，仅支持 "str"、"int"、"bool"、"datetime"')

    def list_deduplicate(self, data_list: List) -> List:
        if data_list is None:
            raise ValueError("data_list 不能为空")
        if not isinstance(data_list, list):
            raise TypeError("data_list 必须为 list 类型")

        seen_hashable = set()
        seen_repr = set()
        result = []
        for item in data_list:
            try:
                key = item
                if key in seen_hashable:
                    continue
                seen_hashable.add(key)
                result.append(item)
            except TypeError:
                # 不可哈希对象使用 repr 去重（保序）
                r = repr(item)
                if r in seen_repr:
                    continue
                seen_repr.add(r)
                result.append(item)
        return result
