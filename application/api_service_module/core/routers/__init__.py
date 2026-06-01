# -*- coding: utf-8 -*-
"""
ApiService 路由 mixins (Task LL #72, Task GGG #93 加 memory)

按业务域把原 1788 行 ApiService.impl.py 拆为 7 个 mixin:
    InvokeRoutesMixin       POST /invoke + WS /invoke/stream
    DocumentsRoutesMixin    /documents/* (upload/list/delete/preview)
    IndexRoutesMixin        /index/build, /index/job/{id}
    AdminRoutesMixin        /health, /healthz, /metrics, /admin/status
    ConfigRoutesMixin       /config/models 4 端点
    MemoryRoutesMixin       /memory/* (list/get/delete/pin/search) — Task GGG
    FrontendRoutesMixin     GET / 和 /ui + static 挂载

所有 mixin 通过 self.app 注册路由, 闭包共享 self 字段, 测试 0 改动.
"""

from .invoke import InvokeRoutesMixin
from .documents import DocumentsRoutesMixin
from .index import IndexRoutesMixin
from .admin import AdminRoutesMixin
from .config import ConfigRoutesMixin
from .memory import MemoryRoutesMixin
from .scheduler import SchedulerRoutesMixin
from .sessions import SessionsRoutesMixin
from .agent import AgentRoutesMixin  # Task FFFF (#123)
from .feedback import FeedbackRoutesMixin  # Task PM-2
from .frontend import FrontendRoutesMixin

__all__ = [
    "InvokeRoutesMixin",
    "DocumentsRoutesMixin",
    "IndexRoutesMixin",
    "AdminRoutesMixin",
    "ConfigRoutesMixin",
    "MemoryRoutesMixin",
    "SchedulerRoutesMixin",
    "SessionsRoutesMixin",
    "AgentRoutesMixin",
    "FeedbackRoutesMixin",
    "FrontendRoutesMixin",
]
