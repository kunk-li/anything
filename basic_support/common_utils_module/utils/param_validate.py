"""参数校验工具类实现。"""

from __future__ import annotations

from typing import Dict, List, Union, Any

from ..core.base import BaseParamValidate


class ParamValidate(BaseParamValidate):
    """参数校验工具类。"""

    def required_validate(self, params: Dict, required_params: List[str]) -> bool:
        if params is None or not isinstance(params, dict):
            raise TypeError("params 必须为 dict 类型")
        if required_params is None or not isinstance(required_params, list):
            raise TypeError("required_params 必须为 list[str] 类型")

        for key in required_params:
            if not isinstance(key, str) or not key.strip():
                raise ValueError("required_params 中存在空 key")
            if not self._has_nested_key(params, key.strip()):
                return False
        return True

    def _has_nested_key(self, params: Dict, dotted_key: str) -> bool:
        cur: Any = params
        for part in dotted_key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return False
            cur = cur[part]
        return True

    def range_validate(self, value: Union[int, float, str], min_val: Union[int, float, int], max_val: Union[int, float, int]) -> bool:
        if min_val is None or max_val is None:
            raise ValueError("min_val/max_val 不能为空")
        if min_val > max_val:
            min_val, max_val = max_val, min_val

        if isinstance(value, (int, float)):
            return min_val <= value <= max_val
        if isinstance(value, str):
            length = len(value)
            return min_val <= length <= max_val
        raise TypeError("value 类型不支持，仅支持 int/float/str")
