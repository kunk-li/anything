"""具体实现类：CommonUtils。

CommonUtils 作为模块统一入口，整合 TextTool/DataTool/ParamValidate/AssistTool。
"""

from __future__ import annotations

from .base import BaseUtils, BaseTextTool, BaseDataTool, BaseParamValidate, BaseAssistTool
from ..utils.text_tool import TextTool
from ..utils.data_tool import DataTool
from ..utils.param_validate import ParamValidate
from ..utils.assist_tool import AssistTool


class CommonUtils(BaseUtils):
    """通用工具总具体实现类，整合所有工具类实例，供上层模块统一调用。"""

    def __init__(self):
        self._text_tool = TextTool()
        self._data_tool = DataTool()
        self._param_validate = ParamValidate()
        self._assist_tool = AssistTool()

    def get_text_tool(self) -> BaseTextTool:
        return self._text_tool

    def get_data_tool(self) -> BaseDataTool:
        return self._data_tool

    def get_param_validate(self) -> BaseParamValidate:
        return self._param_validate

    def get_assist_tool(self) -> BaseAssistTool:
        return self._assist_tool
