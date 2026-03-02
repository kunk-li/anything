from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config_module.core.impl import ConfigManager


@dataclass(frozen=True)
class StateStoreConfig:
    dir: str = "state_store"
    expire_hours: Optional[int] = 24
    max_size: Optional[int] = 1073741824  # 1GB


def load_state_store_config(config_manager: ConfigManager | None = None) -> StateStoreConfig:
    """Load state store config from global ConfigManager.

    Keys:
    - state_store.dir
    - state_store.expire_hours
    - state_store.max_size
    """
    cm = config_manager or ConfigManager()
    cm.load_config()

    store_dir = cm.get_config("state_store.dir", "state_store")
    expire_hours = cm.get_config("state_store.expire_hours", 24)
    max_size = cm.get_config("state_store.max_size", 1073741824)

    # normalize
    try:
        expire_hours = int(expire_hours) if expire_hours is not None else None
    except Exception:
        expire_hours = 24

    try:
        max_size = int(max_size) if max_size is not None else None
    except Exception:
        max_size = 1073741824

    return StateStoreConfig(dir=str(store_dir), expire_hours=expire_hours, max_size=max_size)
