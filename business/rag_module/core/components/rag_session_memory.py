# -*- coding: utf-8 -*-
"""
RagSessionMemoryMixin (从 impl.py 拆出 — 会话历史 + Phase4 记忆个性化, 零行为变更)

    _load_history / _save_turn   会话多轮历史读写 (Task #46)
    _memory_tenant               解析 tenant_id
    _memory_context_block        答前注入 用户画像 + query 相关 fact
    _learn_from_turn             答后从 (query, answer) 抽 fact 入长期记忆 (越用越懂)

依赖 SimpleRAG (self): state_store, history_max_turns, logger,
    memory_enabled, long_term_memory, memory_top_k
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class RagSessionMemoryMixin:
    """会话历史读写 + 记忆个性化 (画像注入 / 跨会话学习)。"""

    def _load_history(self, session_id: Optional[str]) -> List[Dict[str, str]]:
        """从 state_store 读最近 N 轮对话, 返回 [{role,content}, ...] 形式.

        - 未注入 state_store / session_id 为空 / history_max_turns=0 时返回 []
        - 只认 _save_turn 写的 type=="rag" 事件 (共享 session 上别的模块也会写对话事件)
        - 只保留 role/content 字段, 不带 timestamp 等元信息 (LLM 不需要)
        - 严格按 N 轮 (= 2N 条 message, user+assistant 各算 1) 截断
        """
        if not session_id or not self.state_store or self.history_max_turns <= 0:
            return []
        try:
            state = self.state_store.get_state(session_id)
        except Exception as e:
            self.logger.warning(f"读会话状态失败 (忽略): session_id={session_id}, err={e}")
            return []
        if not isinstance(state, dict):
            return []
        events = state.get("events") or []
        max_msgs = self.history_max_turns * 2
        # 先按 type=="rag" + role 过滤, 再截断 (顺序很关键)。
        # events 是按 session_id 共享的混合事件链: 同一会话上 agent_module 也会
        # append role=user/assistant 的对话事件 (type=agent/execution_mode), 还有
        # ReAct 状态事件。只认 _save_turn 自己写的 type=="rag" 事件, 否则会把别的
        # 模块的对话轮当成 RAG 历史喂给 LLM。且必须过滤后再取最近 2N 条, 否则末尾的
        # 跨模块事件会把真正的 RAG 对话轮挤出截断窗口。
        messages: List[Dict[str, str]] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if ev.get("type") != "rag":
                continue
            role = ev.get("role")
            content = ev.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content:
                messages.append({"role": role, "content": content})
        if len(messages) > max_msgs:
            messages = messages[-max_msgs:]
        return messages

    def _save_turn(
        self,
        session_id: Optional[str],
        user_query: str,
        assistant_answer: str,
        trace_id: Optional[str] = None,
    ) -> None:
        """把本轮 (user + assistant) 各 append 一个 event 到 state_store. 失败仅 WARN."""
        if not session_id or not self.state_store:
            return
        try:
            self.state_store.append_event(session_id, {
                "role": "user", "content": user_query,
                "trace_id": trace_id, "type": "rag",
            })
            self.state_store.append_event(session_id, {
                "role": "assistant", "content": assistant_answer,
                "trace_id": trace_id, "type": "rag",
            })
        except Exception as e:
            self.logger.warning(f"写会话状态失败 (忽略): session_id={session_id}, err={e}")

    # ============ Phase4 记忆个性化 (用户模型 + 跨会话学习) ============
    def _memory_tenant(self, request: Dict[str, Any]) -> str:
        """解析当前 tenant_id (extra_params > observability context > default)."""
        ep = request.get("extra_params") or {}
        tenant = ep.get("tenant_id") or request.get("tenant_id")
        if tenant:
            return str(tenant)
        try:
            from observability_module import get_current_tenant
            cur = get_current_tenant()
            if cur:
                return str(cur)
        except Exception:
            pass
        return "default"

    def _memory_context_block(self, query: str, tenant_id: str) -> str:
        """答前注入: 用户画像 + query 相关 fact, 拼成可加到 prompt 前的上下文块。
        无记忆 / 读失败一律返 '' (fail-open, 不影响 RAG 主流程)。"""
        if not self.memory_enabled or self.long_term_memory is None:
            return ""
        blocks: List[str] = []
        try:
            profile = self.long_term_memory.get_user_profile(tenant_id) or {}
            if profile:
                labels = {"preference": "偏好", "style": "风格", "convention": "约定",
                          "domain": "领域", "weakness": "需主动补位"}
                lines = ["[关于使用者 — 回答时遵循; 标 '需主动补位' 的请主动替 ta cover]"]
                for dim, items in profile.items():
                    for it in items:
                        lines.append(f"- [{labels.get(dim, dim)}] {it}")
                blocks.append("\n".join(lines))
        except Exception as e:
            self.logger.warning(f"[memory] 读用户画像失败 (忽略): tenant={tenant_id}, err={e}")
        try:
            from long_term_memory_module import MemoryQuery
            hits = self.long_term_memory.search_facts(
                MemoryQuery(query=query, tenant_id=tenant_id, top_k=self.memory_top_k)
            )
            if hits:
                lines = ["[已知与本问题相关的信息]"] + [f"- {h.fact.content}" for h in hits]
                blocks.append("\n".join(lines))
        except Exception as e:
            self.logger.warning(f"[memory] 查相关 fact 失败 (忽略): tenant={tenant_id}, err={e}")
        return ("\n\n".join(blocks) + "\n\n") if blocks else ""

    def _learn_from_turn(self, query: str, answer: str, tenant_id: str,
                         session_id: Optional[str]) -> None:
        """答后学习: 从 (query, answer) 抽 fact 入长期记忆 (越用越懂)。best-effort,
        无 LLM 抽取通道 (NotImplementedError) / 失败 都静默跳过, 绝不阻断主响应。"""
        if not self.memory_enabled or self.long_term_memory is None or not answer:
            return
        try:
            facts = self.long_term_memory.extract_facts(
                messages=[{"role": "user", "content": query},
                          {"role": "assistant", "content": answer}],
                tenant_id=tenant_id, session_id=session_id,
            )
        except NotImplementedError:
            return
        except Exception as e:
            self.logger.warning(f"[memory] extract_facts 失败 (忽略): tenant={tenant_id}, err={e}")
            return
        for f in facts or []:
            try:
                self.long_term_memory.add_fact(f)
            except Exception as e:
                self.logger.warning(f"[memory] add_fact 失败 (忽略): err={e}")
