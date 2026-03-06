from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from config_module.core.impl import ConfigManager

@dataclass
class LLMCommonConfig:
    max_retry: int = 3
    timeout: int = 30
    batch_size: int = 32
    normalize_vector: bool = True
    media_temp_dir: str = "temp/media"

class LLMAdapterConfig:
    """读取全局配置中的 llm 配置，并提供模块专属默认值兜底。"""

    def __init__(self, config_manager: Optional[ConfigManager] = None):
        self.config_manager = config_manager or ConfigManager()
        # 外部基础支撑层已实现 load_config；此处容错：如果外部已加载则不会重复加载
        try:
            self.config_manager.load_config()
        except Exception:
            # 允许外部系统在更高层提前完成加载；这里不强制失败
            pass

    def get_llm_root(self) -> Dict[str, Any]:
        cfg = self.config_manager.get_config("llm.", {})  # 前缀批量读取
        return cfg if isinstance(cfg, dict) else {}

    def get_common(self) -> LLMCommonConfig:
        root = self.get_llm_root()
        common = root.get("common", {}) if isinstance(root, dict) else {}
        return LLMCommonConfig(
            max_retry=int(common.get("max_retry", 3)),
            timeout=int(common.get("timeout", 30)),
            batch_size=int(common.get("batch_size", 32)),
            normalize_vector=bool(common.get("normalize_vector", True)),
            media_temp_dir=str(common.get("media_temp_dir", "temp/media")),
        )

    def get_default_model(self, request_type: str) -> str:
        root = self.get_llm_root()
        if request_type == "VECTOR":
            return str(root.get("default_vector_model", "default"))
        if request_type == "CHAT":
            return str(root.get("default_chat_model", "default"))
        if request_type == "MULTIMODAL":
            return str(root.get("default_multimodal_model", "default"))
        return "default"

    def get_model_config(self, model_name: str) -> Dict[str, Any]:
        """在 llm 配置树中查找某个 model_name 的配置（跨厂商节点）。"""
        root = self.get_llm_root()
        if not isinstance(root, dict):
            return {}
        for vendor, vendor_cfg in root.items():
            if vendor in {"common", "default_vector_model", "default_chat_model", "default_multimodal_model"}:
                continue
            if not isinstance(vendor_cfg, dict):
                continue
            if model_name in vendor_cfg and isinstance(vendor_cfg[model_name], dict):
                out = dict(vendor_cfg[model_name])
                out["_vendor"] = vendor
                return out
        return {}
