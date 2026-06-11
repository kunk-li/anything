"""Module-level configuration defaults.

The module is designed to read settings from the system ConfigManager. These defaults are used
when configuration is missing or ConfigManager is unavailable.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class DocumentStoreConfig:
    storage_dir: str = "./documents"
    # supported_file_types 已移除: 入库不再按类型清单拦截 (跟 parser 清单漂移过
    # 多次 — py/xml/html 都踩过坑)。文本/二进制把关在 parser 的内容嗅探。
    hash_algorithm: str = "md5"  # md5 | sha256
    zombie_threshold_days: int = 30
    core_doc_prefix: List[str] = field(default_factory=list)
    backup_dir: str = "./backup/zombie_files"
    hash_map_filename: str = ".hash_doc_map.json"  # stored under storage_dir
