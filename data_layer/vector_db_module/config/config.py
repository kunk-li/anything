from __future__ import annotations

from dataclasses import dataclass

try:
    from config_module.core.impl import ConfigManager
except Exception:  # pragma: no cover
    ConfigManager = None  # type: ignore


@dataclass
class VectorDBConfig:
    """向量数据库模块专属配置读取类。

    读取来源：基础支撑层 config_module 的全局配置。
    """

    def __post_init__(self) -> None:
        if ConfigManager is None:
            raise ImportError(
                "config_module 未安装或不可用：请确保系统已包含 config_module，并已加入 PYTHONPATH"
            )
        self.config_manager = ConfigManager()
        # 默认加载 config_module/config/config.yaml（由 ConfigManager 自己决定默认路径）
        self.config_manager.load_config()

    def get_vector_dimension(self) -> int:
        return int(self.config_manager.get_config("vector_db.vector_dimension", 768))

    def get_local_store_dir(self) -> str:
        return str(self.config_manager.get_config("vector_db.local_dir", "vector_store"))

    def get_vector_db_type(self) -> str:
        return str(self.config_manager.get_config("vector_db.type", "faiss"))
