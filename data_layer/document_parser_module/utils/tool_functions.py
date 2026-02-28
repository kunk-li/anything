from __future__ import annotations

import os


def get_file_extension(file_path: str) -> str:
    """获取文件扩展名（小写）。

    Args:
        file_path: 文件路径

    Returns:
        str: 小写扩展名（如 ".pdf"、".md"）
    """
    _, ext = os.path.splitext(file_path)
    return (ext or "").lower()


def check_file_type(file_path: str, supported_types: list) -> bool:
    """校验文件类型是否支持。

    Args:
        file_path: 文件路径
        supported_types: 支持的扩展名列表

    Returns:
        bool: 支持返回 True，否则 False
    """
    ext = get_file_extension(file_path)
    return ext in set([t.lower() for t in supported_types])
