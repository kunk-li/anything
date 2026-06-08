# -*- coding: utf-8 -*-
"""
SimpleAgent 组件 mixins (Task KK #71)

把 SimpleAgent 原 god class 拆成职责清晰的 mixin (Task KK #71 起 4 个; 优化③ 增 1 个):

    ReActEngineMixin     — _react_execute / _generate_plan / _parse_react_response
    ToolExecutorMixin    — _call_tool_with_retry / _tool_cache_* / _needs_approval / _get_tool
    StreamingMixin       — run_stream (Agent 流式版)
    PromptBuilderMixin   — _build_react_prompt / _build_planner_prompt / _parse_planner_response
    SelfMaintenanceMixin — 方向4 自维护 (self_reflect / *_memory_maintenance / run_maintenance_scan …)
    MemoryMixin          — 长期记忆/画像注入 (_inject_long_term_memory / _inject_user_profile / _refine_query …)

设计:
    - 全用 mixin 而非 composition, 保持 self.xxx 调用风格不变, 测试 0 改动
    - SimpleAgent(BaseAgent, ReActEngineMixin, ToolExecutorMixin,
                  StreamingMixin, PromptBuilderMixin) 多重继承
    - mixin 之间不互相 import, 只 import self 字段 (由 SimpleAgent __init__ 提供)
"""

from .react_engine import ReActEngineMixin
from .tool_executor import ToolExecutorMixin
from .streaming import StreamingMixin
from .prompt_builder import PromptBuilderMixin
from .self_maintenance import SelfMaintenanceMixin
from .memory_injection import MemoryMixin
from .task_preprocess import TaskPreprocessMixin, TaskPreContext

__all__ = [
    "ReActEngineMixin",
    "ToolExecutorMixin",
    "StreamingMixin",
    "PromptBuilderMixin",
    "SelfMaintenanceMixin",
    "MemoryMixin",
    "TaskPreprocessMixin",
    "TaskPreContext",
]
