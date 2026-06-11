"""Module-level configuration defaults.

The module is designed to read settings from the system ConfigManager. These defaults are used
when configuration is missing or ConfigManager is unavailable.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class DocumentStoreConfig:
    storage_dir: str = "./documents"
    # 注意: 须覆盖 document_parser_module 的 SUPPORTED_FILE_TYPES (parser 解析出的
    # 文档要能入库) — 两份清单曾漂移过 (py/xml/html 可解析但入库被拒)
    supported_file_types: List[str] = field(default_factory=lambda: [
        "pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt",
        "txt", "md", "rtf", "csv", "json", "py", "xml", "html", "htm"
    ])
    hash_algorithm: str = "md5"  # md5 | sha256
    zombie_threshold_days: int = 30
    core_doc_prefix: List[str] = field(default_factory=list)
    backup_dir: str = "./backup/zombie_files"
    hash_map_filename: str = ".hash_doc_map.json"  # stored under storage_dir
