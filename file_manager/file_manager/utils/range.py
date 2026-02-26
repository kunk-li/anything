from __future__ import annotations

import re
from typing import Optional

from ..core.models import ByteRange

_RANGE_RE = re.compile(r"bytes=(\d+)-(\d+)?$")


def parse_range_header(value: str) -> Optional[ByteRange]:
    """
    解析 Range 头：bytes=start-end
    - 仅支持最常见格式，不支持 suffix range（-500）
    - 返回 None 表示格式不合法
    """
    m = _RANGE_RE.match(value.strip())
    if not m:
        return None
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) is not None else None
    if end is not None and end < start:
        return None
    return ByteRange(start=start, end=end)
