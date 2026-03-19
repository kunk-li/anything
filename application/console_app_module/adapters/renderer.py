from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict

from console_app_module.model.data_model import ConsoleRenderResult
from console_app_module.utils.tool_functions import format_response_text


class BaseRenderer(ABC):
    @abstractmethod
    def render(self, response: Dict, verbose: bool = True) -> ConsoleRenderResult:
        raise NotImplementedError


class PlainRenderer(BaseRenderer):
    def render(self, response: Dict, verbose: bool = True) -> ConsoleRenderResult:
        success = response.get("code") == "SUCCESS"
        title = "执行成功" if success else "执行失败"
        body = format_response_text(response, verbose=verbose)
        footer_parts = []
        if response.get("trace_id"):
            footer_parts.append(f"trace_id={response['trace_id']}")
        if response.get("cost_time") is not None:
            footer_parts.append(f"cost_time={response['cost_time']}")
        footer = " | ".join(footer_parts) if footer_parts else None
        return ConsoleRenderResult(success=success, title=title, body=body, footer=footer, raw_response=response)


class RichLikeRenderer(PlainRenderer):
    def render(self, response: Dict, verbose: bool = True) -> ConsoleRenderResult:
        result = super().render(response, verbose=verbose)
        result.title = f"==== {result.title} ===="
        if result.footer:
            result.footer = f"---- {result.footer} ----"
        return result


class RendererFactory:
    @staticmethod
    def create(name: str) -> BaseRenderer:
        normalized = (name or "plain").lower()
        if normalized == "rich":
            return RichLikeRenderer()
        return PlainRenderer()
