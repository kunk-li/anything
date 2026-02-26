from __future__ import annotations

# 业务异常：核心层只抛这些异常，不包含 HTTP 概念
class FileManagerError(Exception):
    """系统业务异常基类"""


class BadRequest(FileManagerError):
    """参数错误/请求不合法"""


class NotFound(FileManagerError):
    """资源不存在"""


class Conflict(FileManagerError):
    """资源冲突/重复"""


class PayloadTooLarge(FileManagerError):
    """上传内容过大"""


class RangeNotSatisfiable(FileManagerError):
    """Range 超出文件范围（对应 HTTP 416）"""
