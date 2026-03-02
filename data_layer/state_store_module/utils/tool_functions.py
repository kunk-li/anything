from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any, Dict, Tuple


_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def validate_session_id(session_id: str) -> None:
    """Validate session_id to avoid empty/unsafe file names.

    Rules:
    - 1~128 chars
    - starts with alnum
    - allowed: alnum, underscore, hyphen
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id不能为空且必须为字符串")
    if not _SESSION_ID_PATTERN.match(session_id):
        raise ValueError("session_id格式非法：仅允许字母/数字/下划线/短横线，且长度<=128")


def ensure_json_serializable(obj: Any) -> None:
    """Raise TypeError if obj is not JSON serializable."""
    try:
        json.dumps(obj, ensure_ascii=False)
    except Exception as e:
        raise TypeError(f"状态数据不可JSON序列化：{e}") from e


def safe_read_json(path: str) -> Dict[str, Any]:
    """Read JSON file with utf-8 encoding."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    """Atomically write JSON to path to reduce risk of partial writes."""
    ensure_json_serializable(data)
    dir_name = os.path.dirname(path)
    os.makedirs(dir_name, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_state_", suffix=".json", dir=dir_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)  # atomic on POSIX & Windows
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            # best effort
            pass


def get_dir_size_bytes(dir_path: str) -> int:
    total = 0
    for root, _, files in os.walk(dir_path):
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                total += os.path.getsize(fp)
            except OSError:
                continue
    return total


def is_json_file(path: str) -> bool:
    return path.lower().endswith(".json")


def list_session_files(dir_path: str) -> Tuple[str, ...]:
    if not os.path.exists(dir_path):
        return tuple()
    files = []
    for fn in os.listdir(dir_path):
        fp = os.path.join(dir_path, fn)
        if os.path.isfile(fp) and is_json_file(fp):
            files.append(fp)
    return tuple(sorted(files))
