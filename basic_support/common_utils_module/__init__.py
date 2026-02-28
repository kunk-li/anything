"""common_utils_module 模块入口。

暴露 CommonUtils 供上层模块直接导入使用：
    from common_utils_module import CommonUtils
"""

from .core.impl import CommonUtils

__all__ = ["CommonUtils"]
