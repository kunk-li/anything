# -*- coding: utf-8 -*-
"""
StateHistoryMixin (从 impl.py 拆出 — 会话状态/历史持久化, 零行为变更)

把"状态事件写入 + 多轮历史读取 + 聚合状态保存"一组从 SimpleAgent god class 抽到此 mixin:
    _append_state_event   安全写入状态事件 (失败不阻断主任务)
    _load_history         从 state_store 读最近 N 轮对话 [{role, content}, ...]
    _history_prefix       把最近 N 轮拼成可注入 prompt 顶部的 [对话历史] 块
    _save_state_safe      merge 保存聚合状态 + append 本轮 user/assistant 到 events 历史链
    _fallback_session_id  兜底 session id 生成

依赖 SimpleAgent (self) 字段 (由 __init__ 提供):
    state_store, logger, session_prefix
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional


class StateHistoryMixin:
    """会话状态事件写入 / 多轮历史读取 / 聚合状态 merge 保存。"""

    def _append_state_event(
            self,
            session_id: str,
            event_type: str,
            trace_id: Optional[str],
            payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """安全写入状态事件: 失败不阻断主任务."""
        if self.state_store is None:
            return
        event = {
            "session_id": session_id, "event_type": event_type,
            "trace_id": trace_id, "payload": payload or {},
            "created_at": time.time(),
        }
        try:
            if hasattr(self.state_store, "append_event"):
                self.state_store.append_event(session_id, event)
        except Exception as e:
            self.logger.warning(
                f"状态事件写入失败(已忽略): session_id={session_id}, "
                f"event_type={event_type}, error={str(e)}"
            )

    def _load_history(self, session_id, max_turns: int = 6):
        """从 state_store 读最近 N 轮对话, 返回 [{role, content}, ...].

        修 Agent "金鱼记忆": 之前 Agent 完全不读会话历史 (只有 long_term_memory
        facts), 多轮对话不连贯. 现在跟 RAG 一样从 state.events 读最近 N 轮注入 prompt.
        读的是历史 (不含当前轮, 当前轮流式完成后才持久化).
        """
        if not session_id or not self.state_store:
            return []
        try:
            state = self.state_store.get_state(session_id)
        except Exception:
            return []
        if not isinstance(state, dict):
            return []
        events = state.get("events") or []
        max_msgs = max(1, max_turns) * 2
        # 先按 role 过滤再截断: events 是混合日志 (role 对话消息 + ReAct 状态事件
        # react_started/tool_call/... 都 append 到同一条链)。若先 events[-max_msgs:]
        # 截断, 末尾的状态事件会挤掉真正的对话轮, 多轮记忆被静默吃掉。所以先只留
        # role 对话消息, 再对过滤后的列表取最近 max_msgs 条。
        msgs = []
        for ev in events:
            if isinstance(ev, dict):
                role = ev.get("role")
                content = ev.get("content")
                if role in ("user", "assistant") and isinstance(content, str) and content:
                    msgs.append({"role": role, "content": content})
        if len(msgs) > max_msgs:
            msgs = msgs[-max_msgs:]
        return msgs

    def _history_prefix(self, session_id, max_turns: int = 6) -> str:
        """ZZ-5: 把最近 N 轮对话拼成可注入 prompt 顶部的 [对话历史] 块. 无/失败返回 ''.

        ZZ-1 只给默认流式 (_run_stream_direct) 注入了历史; ReAct / plan / 非流式 execute
        / single_shot 仍是金鱼记忆. 这个 helper 给那几条路径统一补多轮上下文.
        """
        try:
            history = self._load_history(session_id, max_turns=max_turns)
        except Exception:
            return ""
        lines = []
        for h in history:
            who = "用户" if h.get("role") == "user" else "助手"
            c = (h.get("content") or "").strip()
            if c:
                lines.append(f"{who}: {c}")
        if not lines:
            return ""
        return "[对话历史]\n" + "\n".join(lines) + "\n\n---\n\n"

    def _save_state_safe(
            self,
            session_id: str,
            state: Dict[str, Any],
            trace_id: Optional[str],
    ) -> None:
        """安全保存聚合状态 + append 本轮 user/assistant 到 events 历史链.

        之前 bug (full-replace): save_state 整存, 每轮覆盖前轮, 切回历史只看到最后一条.

        再之前的 merge 写法 (get_state + 重建 events + 全量 save_state) 仍有 lost-update
        竞态: get_state 与 save_state 是两次独立的 per-session 加锁, 中间若有并发
        append_event / 另一轮 _save_state_safe, 各读旧 events 各全量写回 → 后写覆盖先写,
        丢事件 (findings #2/#3)。

        现在按 store 已有的原子原语拆成两类、不再在 agent 层做 events 的读-改-写:
          1. 对话历史 (events 链): 本轮 user_task / assistant_answer 各走一次
             append_event —— 它在 per-session 锁内 get→append→write 整段串行, 天然
             race-free, 且只追加不覆盖。
          2. 顶层标量 (status/task/answer/...): 走 merge_state —— store 持 per-session
             锁做 get→浅合并(不动 events)→write, 与并发 append 互斥且不互相覆盖
             (events 与标量是不相交的 key)。merge_state 不可用时降级到 save_state,
             但此时只写标量、绝不带 events, 避免再把 events 清空。
        失败不阻断主任务.
        """
        if self.state_store is None:
            return
        try:
            new_state = dict(state) if isinstance(state, dict) else {}

            # 1. 本轮 task / answer → 各 append 一个 role event (原子, 不丢事件)
            #    task 用 state.task (已经是 original_task, 不含长期记忆 prefix)
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()))
            user_task = new_state.get("task")
            asst_answer = new_state.get("answer")
            event_type = new_state.get("execution_mode") or "agent"
            has_append = hasattr(self.state_store, "append_event")
            if has_append and user_task:
                self.state_store.append_event(session_id, {
                    "role": "user",
                    "content": str(user_task),
                    "timestamp": now_iso,
                    "trace_id": trace_id,
                    "type": event_type,
                })
            if has_append and asst_answer:
                self.state_store.append_event(session_id, {
                    "role": "assistant",
                    "content": str(asst_answer),
                    "timestamp": now_iso,
                    "trace_id": trace_id,
                    "type": event_type,
                })

            # 2. 顶层标量字段 (list_sessions 抽 title / 前端 Path-3 兜底用), 不含 events
            scalars = {k: v for k, v in new_state.items() if k != "events"}
            if scalars:
                if hasattr(self.state_store, "merge_state"):
                    # 锁内读-改-写, 只浅合并标量、保留 events: race-free
                    self.state_store.merge_state(session_id, scalars)
                elif not has_append and hasattr(self.state_store, "save_state"):
                    # 后端既无 append_event 也无 merge_state (老桩): 退回整存。
                    # 此分支不写 events (events 已无原子写入手段), 仅落标量。
                    self.state_store.save_state(session_id, scalars)
        except Exception as e:
            self.logger.warning(
                f"状态保存失败(已忽略): session_id={session_id}, "
                f"trace_id={trace_id}, error={str(e)}"
            )

    def _fallback_session_id(self) -> str:
        """仅兼容兜底使用, 不作为主路径."""
        return f"{self.session_prefix}_{uuid.uuid4().hex[:12]}"
