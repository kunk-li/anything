from __future__ import annotations

import uuid


def make_storage_key(tenant_id: str, filename: str) -> str:
    """
    生成存储 key：
    - tenant_id 做隔离前缀
    - 使用 uuid 保证唯一
    - 保留扩展名（可选，但对调试友好）
    """
    ext: str = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()

    return f"{tenant_id}/{uuid.uuid4().hex}{ext}"
