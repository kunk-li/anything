from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Iterator, List, Optional


class BaseInputProvider(ABC):
    @abstractmethod
    def read_line(self, prompt: str) -> Optional[str]:
        raise NotImplementedError


class StdinInputProvider(BaseInputProvider):
    def read_line(self, prompt: str) -> Optional[str]:
        try:
            return input(prompt)
        except EOFError:
            return None


class ListInputProvider(BaseInputProvider):
    def __init__(self, lines: Iterable[str]):
        self._iterator: Iterator[str] = iter(lines)

    def read_line(self, prompt: str) -> Optional[str]:
        del prompt
        return next(self._iterator, None)
