# -*- coding: utf-8 -*-
"""
SimpleAgent 组件 mixins (Task KK #71)

把 SimpleAgent 原 1764 行的 god class 拆成 4 个职责清晰的 mixin:

    ReActEngineMixin     — _react_execute / _generate_plan / _parse_react_response
    ToolExecutorMixin    — _call_tool_with_retry / _tool_cache_* / _needs_approval / _get_tool
    StreamingMixin       — run_stream (Agent 流式版)
    PromptBuilderMixin   — _build_react_prompt / _build_planner_prompt / _parse_planner_response

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

__all__ = [
    "ReActEngineMixin",
    "ToolExecutorMixin",
    "StreamingMixin",
    "PromptBuilderMixin",
]
