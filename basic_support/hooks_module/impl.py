# -*- coding: utf-8 -*-
"""
Hooks 系统 (Task Z #60)

Claude Code 风格 hook framework. 给 Agent / RAG 调工具或 LLM 前后插一个钩子链,
hook 可:
    - 看 / 改 输入 (返回 modified input)
    - 看 / 改 结果 (返回 modified result)
    - 抛 BlockedError 中断后续 hook 链 + 主链路 (返回 envelope error)
    - 抛任意其他异常 → 该 hook 被忽略, 链路继续 (失败不阻塞)

挂载形态:
    pre_tool_call (tool_name, input_data, ctx)   → None | modified_input | raise BlockedError
    post_tool_call (tool_name, input_data, output, ctx) → None | modified_output
    pre_llm_call  (prompt, model, ctx)           → None | modified_prompt | raise BlockedError
    post_llm_call (prompt, model, response, ctx) → None | modified_response

ctx 是个 dict, 主链路传上下文 (trace_id / session_id / tenant_id / iteration ...).
hook 之间通过 ctx 共享状态 (例: pre_tool_call 在 ctx['_rate_remain'] 写, post 读).

注册方式:
    from common_utils_module import get_hook_registry
    reg = get_hook_registry()
    reg.add_pre_tool_call(my_hook_fn)

或装饰器:
    @get_hook_registry().pre_tool_call
    def my_hook(tool_name, input_data, ctx): ...

线程安全: 注册 / 触发都过 lock. hook 列表是 copy-on-iterate 防止迭代中改.

设计参考: Anthropic Claude Code hooks (pre_tool_call / post_tool_call / pre_compact 等)
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional


class BlockedError(RuntimeError):
    """Hook 抛这个异常表示 "拒绝当前调用". 主链路 catch 后包装成 envelope error.

    code 默认 HOOK_BLOCKED, 调用方可改成业务码 (RATE_LIMITED / AUTH_REQUIRED 等).
    """
    def __init__(self, message: str, code: str = "HOOK_BLOCKED", details: Optional[Dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


HookFn = Callable[..., Any]


class HookRegistry:
    """4 类 hook 的集中注册表 + 触发器."""

    EVENTS = ("pre_tool_call", "post_tool_call", "pre_llm_call", "post_llm_call")

    # 每个 event 中 "可被 hook 修改的那个 positional arg" 的索引.
    # fire() 用这个把上个 hook 的返回值塞回 args, 让下一个 hook 看到的是 chain 后的值.
    # 顺序参考 hook 签名:
    #   pre_tool_call(tool_name, input_data, ctx)               -> 改 input_data (idx=1)
    #   post_tool_call(tool_name, input_data, output, ctx)      -> 改 output (idx=2)
    #   pre_llm_call(prompt, model, ctx)                         -> 改 prompt (idx=0)
    #   post_llm_call(prompt, model, response, ctx)              -> 改 response (idx=2)
    _MUTABLE_ARG_INDEX = {
        "pre_tool_call": 1,
        "post_tool_call": 2,
        "pre_llm_call": 0,
        "post_llm_call": 2,
    }

    def __init__(self) -> None:
        self._hooks: Dict[str, List[HookFn]] = {ev: [] for ev in self.EVENTS}
        self._lock = threading.Lock()

    # ---- 注册 API ----
    def add(self, event: str, fn: HookFn) -> HookFn:
        if event not in self._hooks:
            raise ValueError(f"unknown hook event: {event}; allowed: {self.EVENTS}")
        with self._lock:
            self._hooks[event].append(fn)
        return fn

    def add_pre_tool_call(self, fn: HookFn) -> HookFn:  return self.add("pre_tool_call", fn)
    def add_post_tool_call(self, fn: HookFn) -> HookFn: return self.add("post_tool_call", fn)
    def add_pre_llm_call(self, fn: HookFn) -> HookFn:   return self.add("pre_llm_call", fn)
    def add_post_llm_call(self, fn: HookFn) -> HookFn:  return self.add("post_llm_call", fn)

    # 装饰器写法
    def pre_tool_call(self, fn: HookFn) -> HookFn:  return self.add("pre_tool_call", fn)
    def post_tool_call(self, fn: HookFn) -> HookFn: return self.add("post_tool_call", fn)
    def pre_llm_call(self, fn: HookFn) -> HookFn:   return self.add("pre_llm_call", fn)
    def post_llm_call(self, fn: HookFn) -> HookFn:  return self.add("post_llm_call", fn)

    # ---- 触发 ----
    def fire(self, event: str, *args, **kwargs) -> Any:
        """跑该 event 的所有 hook, 链式传递修改值给下一个 hook.

        如果 hook 返回非 None, 且该 event 在 _MUTABLE_ARG_INDEX 中, 则把这个返回值
        塞回 args 的对应位置, 让下一个 hook 看到的就是已修改的版本.

        返回: 最后一个非 None 的返回值; 全 None 则返回 None.
        BlockedError 不被吞 — 直接往上传.
        其他异常被吞 (这个 hook 被跳过), 链路继续.
        """
        if event not in self._hooks:
            return None
        with self._lock:
            hooks = list(self._hooks[event])  # snapshot
        result: Any = None
        args_list = list(args)
        mut_idx = self._MUTABLE_ARG_INDEX.get(event)
        for fn in hooks:
            try:
                ret = fn(*args_list, **kwargs)
                if ret is not None:
                    result = ret
                    if mut_idx is not None and mut_idx < len(args_list):
                        args_list[mut_idx] = ret
            except BlockedError:
                raise
            except Exception:
                # 不让一个坏 hook 拖垮所有 hook
                pass
        return result

    # ---- 工具方法 ----
    def list(self, event: Optional[str] = None) -> Dict[str, List[str]]:
        """返回每个 event 注册了哪些 hook (函数名), 给 admin 面板看."""
        with self._lock:
            if event:
                return {event: [getattr(f, "__name__", repr(f)) for f in self._hooks.get(event, [])]}
            return {
                ev: [getattr(f, "__name__", repr(f)) for f in fns]
                for ev, fns in self._hooks.items()
            }

    def count(self) -> Dict[str, int]:
        with self._lock:
            return {ev: len(fns) for ev, fns in self._hooks.items()}

    def clear(self, event: Optional[str] = None) -> None:
        """主要给测试用. event=None 则全清."""
        with self._lock:
            if event:
                self._hooks[event] = []
            else:
                for ev in self.EVENTS:
                    self._hooks[ev] = []


# 模块级单例
_default: Optional[HookRegistry] = None
_default_lock = threading.Lock()


def get_hook_registry() -> HookRegistry:
    global _default
    with _default_lock:
        if _default is None:
            _default = HookRegistry()
        return _default


def reset_hook_registry() -> None:
    """主要给测试用. 清空单例 (而不是只清 hook 列表) 让单例彻底重建."""
    global _default
    with _default_lock:
        _default = None
