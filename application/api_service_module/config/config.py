"""API服务模块配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class ApiServiceConfig:
    """API 服务模块配置对象。"""

    app_name: str = field(default_factory=lambda: os.getenv("API_APP_NAME", "RAG Agent API Service"))
    app_version: str = field(default_factory=lambda: os.getenv("API_APP_VERSION", "1.0.0"))
    default_type: str = field(default_factory=lambda: os.getenv("API_DEFAULT_TYPE", "rag"))
    default_top_k: int = field(default_factory=lambda: int(os.getenv("API_DEFAULT_TOP_K", "5")))
    enable_trace: bool = field(default_factory=lambda: os.getenv("API_ENABLE_TRACE", "true").lower() == "true")
    auth_enabled: bool = field(default_factory=lambda: os.getenv("API_AUTH_ENABLED", "false").lower() == "true")
    api_keys: List[str] = field(
        default_factory=lambda: [item.strip() for item in os.getenv("API_KEYS", "").split(",") if item.strip()]
    )
    max_request_size: int = field(default_factory=lambda: int(os.getenv("API_MAX_REQUEST_SIZE", str(1024 * 1024))))
    upload_dir: str = field(default_factory=lambda: os.getenv("API_UPLOAD_DIR", "/tmp/api_service_uploads"))
    index_result_dir: str = field(default_factory=lambda: os.getenv("API_INDEX_RESULT_DIR", "/tmp/api_service_indexes"))
    readiness_name: str = "/ready"
    liveness_name: str = "/live"
