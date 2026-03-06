from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

# 依赖基础支撑层配置模块（外部已实现）
from config_module.core.impl import ConfigManager

@dataclass
class LLMAdapterConfig:
    """大模型对接模块配置读取封装（从全局config读取，不维护独立yaml）"""

    def __init__(self, config_manager: Optional[ConfigManager] = None):
        self.config_manager = config_manager or ConfigManager()
        # 外部基础支撑层已实现 load_config；此处容错：如果外部已加载则不会重复加载
        try:
            self.config_manager.load_config()
        except Exception:
            # 允许外部系统在更高层提前完成加载；这里不强制失败
            pass

    def get_llm_root(self) -> Dict[str, Any]:
        return self.config_manager.get_config("llm.", {}) if hasattr(self.config_manager, "get_config") else {}

    def get_common(self) -> Dict[str, Any]:
        return self.config_manager.get_config("llm.common", {})  # type: ignore

    def get_model_config(self, model_name: str) -> Dict[str, Any]:
        # 配置结构：llm.<vendor>.<model_name>.<...>
        llm_cfg = self.config_manager.get_config("llm.", {})  # type: ignore
        if not isinstance(llm_cfg, dict):
            return {}
        # 扫描厂商节点
        for vendor, vendor_cfg in llm_cfg.items():
            if vendor in {"default_vector_model", "default_chat_model", "default_multimodal_model", "common"}:
                continue
            if isinstance(vendor_cfg, dict) and model_name in vendor_cfg:
                cfg = vendor_cfg.get(model_name, {})
                if isinstance(cfg, dict):
                    cfg = dict(cfg)
                    cfg["_vendor"] = vendor
                    return cfg
        return {}

    def get_default_model(self, request_type: str) -> str:
        if request_type == "VECTOR":
            return self.config_manager.get_config("llm.default_vector_model", "default")  # type: ignore
        if request_type == "CHAT":
            return self.config_manager.get_config("llm.default_chat_model", "default")  # type: ignore
        if request_type == "MULTIMODAL":
            return self.config_manager.get_config("llm.default_multimodal_model", "default")  # type: ignore
        return "default"

    def get_retry(self) -> int:
        common = self.get_common()
        return int(common.get("max_retry", 3)) if isinstance(common, dict) else 3

    def get_timeout(self) -> int:
        common = self.get_common()
        return int(common.get("timeout", 30)) if isinstance(common, dict) else 30

    def get_media_temp_dir(self) -> str:
        common = self.get_common()
        return str(common.get("media_temp_dir", "temp/media")) if isinstance(common, dict) else "temp/media"
