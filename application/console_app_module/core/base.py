from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List

from console_app_module.model.data_model import ConsoleInput, ConsoleRenderResult


class BaseConsoleApp(ABC):
    @abstractmethod
    def run(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def parse_input(self, text: str) -> ConsoleInput:
        raise NotImplementedError

    @abstractmethod
    def build_request(self, source: ConsoleInput | Dict[str, Any] | str) -> Dict[str, Any]:
        """source 可为 ConsoleInput / dict / 原始 str, 统一构造 request body。"""
        raise NotImplementedError

    @abstractmethod
    def render_response(self, response: Dict[str, Any]) -> ConsoleRenderResult:
        raise NotImplementedError

    @abstractmethod
    def handle_command(self, console_input: ConsoleInput) -> bool:
        raise NotImplementedError

    @abstractmethod
    def run_batch(self, items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def export_history(self, export_path: str, fmt: str = "json") -> str:
        raise NotImplementedError
