from __future__ import annotations

import csv
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

from console_app_module.model.data_model import ConsoleHistoryItem


class BaseHistoryStore(ABC):
    @abstractmethod
    def append(self, item: ConsoleHistoryItem) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_items(self, session_id: Optional[str] = None) -> List[ConsoleHistoryItem]:
        raise NotImplementedError

    @abstractmethod
    def export(self, path: str, fmt: str = "json") -> str:
        raise NotImplementedError


class InMemoryHistoryStore(BaseHistoryStore):
    def __init__(self, max_size: int = 500):
        self.max_size = max_size
        self._items: List[ConsoleHistoryItem] = []

    def append(self, item: ConsoleHistoryItem) -> None:
        self._items.append(item)
        if len(self._items) > self.max_size:
            self._items = self._items[-self.max_size :]

    def list_items(self, session_id: Optional[str] = None) -> List[ConsoleHistoryItem]:
        if session_id is None:
            return list(self._items)
        return [item for item in self._items if item.session_id == session_id]

    def export(self, path: str, fmt: str = "json") -> str:
        fmt = fmt.lower()
        export_path = Path(path)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        items = [item.to_dict() for item in self._items]
        if fmt == "json":
            export_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        elif fmt == "md":
            lines = ["# Console History", ""]
            for idx, item in enumerate(items, start=1):
                lines.extend([
                    f"## {idx}",
                    f"- timestamp: {item['timestamp']}",
                    f"- session_id: {item['session_id']}",
                    f"- request: `{json.dumps(item['request'], ensure_ascii=False)}`",
                    f"- response: `{json.dumps(item['response'], ensure_ascii=False)}`",
                    "",
                ])
            export_path.write_text("\n".join(lines), encoding="utf-8")
        elif fmt == "csv":
            with export_path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=["timestamp", "session_id", "duration_ms", "source", "request", "response", "tags"])
                writer.writeheader()
                for item in items:
                    writer.writerow({
                        "timestamp": item["timestamp"],
                        "session_id": item["session_id"],
                        "duration_ms": item["duration_ms"],
                        "source": item["source"],
                        "request": json.dumps(item["request"], ensure_ascii=False),
                        "response": json.dumps(item["response"], ensure_ascii=False),
                        "tags": json.dumps(item["tags"], ensure_ascii=False),
                    })
        else:
            raise ValueError(f"不支持的导出格式: {fmt}")
        return str(export_path)
