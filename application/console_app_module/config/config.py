from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


CONSOLE_APP_CONFIG: Dict[str, Any] = {
    "default_mode": "rag",
    "default_top_k": 5,
    "default_verbose": True,
    "default_renderer": "plain",
    "enable_multiline": True,
    "enable_batch": True,
    "enable_script_mode": True,
    "history_max_size": 500,
    "export_default_format": "json",
    "batch_continue_on_error": True,
    "show_trace_id": True,
    "show_cost_time": True,
}


def load_console_config(overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    config = deepcopy(CONSOLE_APP_CONFIG)
    if overrides:
        config.update(overrides)
    return config
