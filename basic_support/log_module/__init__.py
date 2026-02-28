"""log_module package

External usage:
    from log_module import SystemLogger
    logger = SystemLogger()
    logger.info("hello")
"""

from .core.base import BaseLogger
from .core.impl import SystemLogger

__all__ = ["BaseLogger", "SystemLogger"]
