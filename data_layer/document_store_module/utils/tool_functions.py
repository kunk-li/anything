from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

# External logger is optional for standalone usage.
try:
    from log_module.core.impl import SystemLogger  # type: ignore
except Exception:  # pragma: no cover
    class SystemLogger:  # minimal fallback
        def __init__(self, module_name: str = "tool_functions"):
            self.module_name = module_name
        def debug(self, msg: str, *args, **kwargs): pass
        def info(self, msg: str, *args, **kwargs): pass
        def warning(self, msg: str, *args, **kwargs): pass
        def error(self, msg: str, *args, **kwargs): pass
        def critical(self, msg: str, *args, **kwargs): pass

logger = SystemLogger(module_name="tool_functions")

ILLEGAL_FILE_NAME_CHARS = set('/\\:*?"<>|')  # cross-platform unsafe chars


def now_str() -> str:
    """Return current time string in 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def generate_doc_id() -> str:
    """Generate a UUID4 string for doc_id."""
    return str(uuid.uuid4())


def is_uuid4(doc_id: str) -> bool:
    """Validate doc_id is a UUID4 string."""
    try:
        u = uuid.UUID(doc_id, version=4)
        return str(u) == doc_id.lower()
    except Exception:
        return False


def sanitize_file_name(file_name: str, file_type: str) -> str:
    """Return a safe file name; if invalid, generate default_{timestamp}.{file_type}."""
    if not file_name or any(ch in ILLEGAL_FILE_NAME_CHARS for ch in file_name):
        safe = f"default_{int(time.time())}.{file_type or 'txt'}"
        logger.warning(f"Illegal/empty file_name provided; replaced with {safe}")
        return safe
    return file_name


def normalize_file_type(file_type: str) -> str:
    """Normalize file_type to lower-case without leading dot."""
    if not file_type:
        return "unknown"
    ft = file_type.lower().lstrip(".")
    return ft or "unknown"


def get_document_path(storage_dir: str, doc_id: str, file_type: str) -> str:
    """Compute document storage file path: {storage_dir}/{doc_id}.{file_type}."""
    ft = normalize_file_type(file_type)
    return os.path.join(storage_dir, f"{doc_id}.{ft}")


def get_info_file_path(storage_dir: str, doc_id: str) -> str:
    """Compute info file path: {storage_dir}/{doc_id}.info.json."""
    return os.path.join(storage_dir, f"{doc_id}.info.json")


def get_file_size(file_path: str) -> int:
    """Get file size in bytes; return 0 if missing or error."""
    try:
        return os.path.getsize(file_path)
    except Exception as e:
        logger.warning(f"get_file_size failed for {file_path}: {e}")
        return 0


def calculate_content_hash(content: str, algorithm: str = "md5") -> str:
    """Calculate content hash (md5 or sha256) for UTF-8 encoded content."""
    if content is None or content == "":
        raise ValueError("content不能为空，无法计算哈希")
    algo = (algorithm or "md5").lower()
    data = content.encode("utf-8")
    if algo == "md5":
        return hashlib.md5(data).hexdigest()
    if algo == "sha256":
        return hashlib.sha256(data).hexdigest()
    raise ValueError(f"不支持的哈希算法：{algorithm}（仅支持md5/sha256）")


def is_duplicate_aux(existing_docs: List[Dict[str, str]], file_name: str, file_type: str, content_length: int) -> Optional[str]:
    """Supplementary duplicate check by file_name, file_type and content_length."""
    for info in existing_docs:
        try:
            if (
                info.get("file_name") == file_name
                and normalize_file_type(info.get("file_type", "")) == normalize_file_type(file_type)
                and int(info.get("content_length", -1)) == int(content_length)
            ):
                return info.get("doc_id")
        except Exception:
            continue
    return None


def parse_time(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")


def is_zombie_file(doc_info: Dict[str, str], threshold_days: int) -> bool:
    """Type2 zombie: long time since last_access_time and update_time."""
    if threshold_days < 0:
        raise ValueError("threshold_days不能小于0")
    last_access = doc_info.get("last_access_time")
    update_time = doc_info.get("update_time")
    if not last_access or not update_time:
        # missing timestamps => treat as zombie candidate (conservative for cleanup routines that also check integrity)
        return True
    try:
        la = parse_time(last_access)
        ut = parse_time(update_time)
    except Exception as e:
        raise ValueError(f"时间格式异常：{e}") from e
    now = datetime.now()
    delta_days = max((now - la).days, (now - ut).days)
    return delta_days > threshold_days


def backup_file(file_path: str, backup_dir: str) -> bool:
    """Backup file to backup_dir as {name}_{timestamp}{ext}."""
    try:
        if not os.path.exists(file_path):
            return False
        os.makedirs(backup_dir, exist_ok=True)
        base = os.path.basename(file_path)
        name, ext = os.path.splitext(base)
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        target = os.path.join(backup_dir, f"{name}_{ts}{ext}")
        shutil.copy2(file_path, target)
        return True
    except Exception as e:
        logger.error(f"backup_file failed for {file_path}: {e}")
        return False


def json_dump(data: Dict, path: str) -> None:
    """Write JSON to path in UTF-8."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def json_load(path: str) -> Dict:
    """Read JSON from path."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
