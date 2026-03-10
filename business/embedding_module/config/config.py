from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class EmbeddingModuleConfig:
    """
    模块内默认配置。
    正式接入 config_module 后，可以由 ConfigManager 统一覆盖。
    """

    defaults: Dict[str, Any] = field(
        default_factory=lambda: {
            "embedding.model_name": "all-MiniLM-L6-v2",
            "vector_db.vector_dimension": 384,
            "llm.common.normalize_vector": True,
            "llm.common.batch_size": 32,
            "llm.default_vector_model": "text-embedding-ada-002",
        }
    )

    def get(self, key: str, default: Any = None) -> Any:
        return self.defaults.get(key, default)