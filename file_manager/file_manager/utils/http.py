from __future__ import annotations

from urllib.parse import quote


def build_content_disposition(filename: str) -> str:
    """
    生成 Content-Disposition，兼容中文文件名（RFC 5987）
    - HTTP header 必须能被 latin-1 编码，因此不能直接塞中文到 filename=""
    - 采用：filename="ASCII_fallback"; filename*=UTF-8''urlencoded
    """
    # ASCII fallback：把非 ASCII 或危险字符替换成下划线
    fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in {'"', "\\", ";"} else "_"
        for ch in filename
    )
    if not fallback:
        fallback = "download"

    # RFC 5987：UTF-8 + URL 编码
    encoded = quote(filename, safe="")
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'
