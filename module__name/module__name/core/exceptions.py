from __future__ import annotations

# 业务异常：核心层只抛这些异常，不包含 HTTP 概念
class module__nameManagerError(Exception):
    """系统业务异常基类"""


class BadRequest(module__nameManagerError):
    """参数错误/请求不合法"""


class NotFound(module__nameManagerError):
    """资源不存在"""


class Conflict(module__nameManagerError):
    """资源冲突/重复"""


class PayloadTooLarge(module__nameManagerError):
    """上传内容过大"""


class RangeNotSatisfiable(module__nameManagerError):
    """Range 超出文件范围（对应 HTTP 416）"""
