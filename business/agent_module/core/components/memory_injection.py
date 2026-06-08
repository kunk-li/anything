# -*- coding: utf-8 -*-
"""
MemoryMixin (优化③ 从 impl.py 拆出 — 长期记忆 / 用户画像 集成)

把"长期记忆 + 画像"一整组从 SimpleAgent god class 抽到此 mixin (零行为变更):
    _memory_tenant              从 request / observability context 解析 tenant
    _inject_long_term_memory    查 query 相关 fact → 拼进 task 前 (Task FFF #92)
    _inject_user_profile        always-on 注入用户画像 (UP-3, 方向1)
    _refine_query               含糊问题基于画像改写 / ask 模式反问澄清 (UP-4, 方向1阶段2)
    _extract_and_store_memory   任务收尾把 (task, answer) 抽 fact 入库

依赖 SimpleAgent (self) 字段/方法 (由 __init__ 及其它 mixin / impl 提供):
    long_term_memory, logger, memory_top_k, query_refine_max_len,
    _resolve_llm_planner (impl), _parse_reflection_json (impl, 通用 LLM-JSON 解析)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class MemoryMixin:
    """长期记忆 + 用户画像注入 / query refinement / fact 抽取入库。"""

    def _memory_tenant(self, request: Dict[str, Any]) -> str:
        """从 request / observability context 解析当前 tenant_id, 兜底 default."""
        tenant = (request.get("extra_params") or {}).get("tenant_id")
        if tenant:
            return str(tenant)
        # 走 observability_module 的 context var (ApiService middleware 设的)
        try:
            from observability_module import get_current_tenant
            cur = get_current_tenant()
            if cur:
                return str(cur)
        except Exception:
            pass
        return "default"

    def _inject_long_term_memory(
        self, task: str, tenant_id: str, trace_id: Optional[str],
    ) -> tuple:
        """从长期记忆查相关 fact, 拼成 context block 加到 task 前面.

        返回 (augmented_task, hits_summary_list).
        hits_summary_list 用于响应 details["memory_hits"], 方便前端显示
        "Agent 用到了 X 条长期记忆".
        """
        try:
            from long_term_memory_module import MemoryQuery
        except Exception:
            return task, []

        query = MemoryQuery(
            query=task, tenant_id=tenant_id, top_k=self.memory_top_k,
        )
        hits = self.long_term_memory.search_facts(query)
        if not hits:
            return task, []

        lines = ["[长期记忆 — 已知关于用户/上下文的相关信息]"]
        summary: List[Dict[str, Any]] = []
        for h in hits:
            lines.append(f"- {h.fact.content}")
            summary.append({
                "fact_id": h.fact.fact_id,
                "content": h.fact.content[:120],
                "score": round(h.score, 3),
                "reason": h.reason,
            })
        lines.append("")
        lines.append("[当前任务]")
        lines.append(task)
        augmented = "\n".join(lines)
        self.logger.info(
            f"[memory] 注入 {len(hits)} 条 fact 到 task, "
            f"tenant={tenant_id}, trace_id={trace_id}"
        )
        return augmented, summary

    def _inject_user_profile(self, task: str, tenant_id: str) -> str:
        """UP-3 (方向1): always-on 注入用户画像 ("这个人怎么做事"), 不依赖 query。
        区别于 _inject_long_term_memory(只注入 query 相关 fact); 画像始终注入。
        weakness 维度 → "需主动补位" 提示, 让 agent 主动 cover 使用者的不足 (规避缺陷)。"""
        profile = self.long_term_memory.get_user_profile(tenant_id)
        if not profile:
            return task
        labels = {"preference": "偏好", "style": "风格", "convention": "约定",
                  "domain": "领域", "weakness": "需主动补位"}
        lines = ["[关于使用者 — 始终遵循; 标 '需主动补位' 的项请主动替 ta cover]"]
        for dim, items in profile.items():
            for it in items:
                lines.append(f"- [{labels.get(dim, dim)}] {it}")
        lines.append("")
        return "\n".join(lines) + task

    def _refine_query(
        self, task: str, tenant_id: str, trace_id: Optional[str], mode: str = "auto",
    ) -> tuple:
        """UP-4 (方向1 阶段2): 用户问得含糊时, 基于画像把问题补全/澄清后再规划。

        区别于 _inject_user_profile(把画像当上下文附在 task 前): 这里直接改写"问题本身",
        让规划/检索拿到的是"用户本来就想问的更精确版本"。

        mode: "auto"=歧义时静默改写(原行为) / "ask"=歧义大时反问澄清(不改写, 返澄清问题)。
        返回 (result, meta)。任一前提不满足 (问题已清楚 / 无画像 / 无 LLM / 解析失败
        / 安全阀拦截) 都返回 (task, None) — 主链路用原问题, 零副作用 (fail-open)。
        auto 命中: result=改写后问题, meta={"action":"rewrite","original","refined","reason"};
        ask 命中:  result=原 task(不改写), meta={"action":"clarify","original","question","reason"}
                   → 由 execute 早退, 把 question 作为回答返回, 不执行任务。
        meta 由 execute 记到 details 透明可见。

        设计原则 (minimal-change + human-in-loop): 仅在确有歧义时介入, 严格保留原意,
        auto 只补"知道使用者偏好就能确定的缺省"(语言/技术栈/格式/范围/默认值),
        绝不替用户臆造其没表达的新需求; ask 则把不确定交还用户决定 (反问而非臆测)。
        """
        # gate 0: 已足够具体的长问题不折腾 (省 LLM, 也避免画蛇添足)
        if len(task) > self.query_refine_max_len:
            return task, None
        # gate 1: "基于画像优化" 的前提是有画像
        profile = self.long_term_memory.get_user_profile(tenant_id)
        if not profile:
            return task, None
        # gate 2: 需要 LLM 通道做"判含糊 + 改写/反问"
        llm = self._resolve_llm_planner(trace_id=trace_id)
        if llm is None:
            return task, None

        labels = {"preference": "偏好", "style": "风格", "convention": "约定",
                  "domain": "领域", "weakness": "需主动补位"}
        profile_lines = [f"- [{labels.get(dim, dim)}] {it}"
                         for dim, items in profile.items() for it in items]
        is_ask = str(mode).strip().lower() == "ask"
        if is_ask:
            prompt = (
                "你在帮一个 AI 助手判断是否需要先向用户澄清问题。下面给出用户画像和用户刚提的问题。\n"
                "判断该问题是否含糊到 '直接执行很可能跑偏、应先反问用户澄清' 的程度:\n"
                "1. 若问题已足够清楚, 或缺省靠画像就能安全补全 → needs_clarify=false。\n"
                "2. 若歧义较大 (多种合理理解 / 关键信息缺失, 画像也定不了) → needs_clarify=true, "
                "并给出一个简短、具体、单一焦点的澄清问题(中文), 帮用户把需求说清。\n"
                "3. clarify_question 是反问用户的问题, 不是你对问题的回答, 也不要替用户臆测答案。\n"
                f"\n[用户画像]\n" + "\n".join(profile_lines) +
                f"\n\n[用户的问题]\n{task}\n"
                '\n只输出 JSON(不要解释): '
                '{"needs_clarify": true/false, "clarify_question": "一句话澄清问题(needs_clarify=false 时留空)", '
                '"reason": "一句话说明为何要澄清(中文)"}'
            )
        else:
            prompt = (
                "你在帮一个 AI 助手澄清用户的问题。下面给出这个用户的画像, 和用户刚提的问题。\n"
                "判断该问题是否含糊/缺省到了 '知道用户偏好就能问得更准' 的程度, 并按规则处理:\n"
                "1. 若问题已清楚完整, 或含糊点只能靠对话上文(而非画像)补 → needs_refine=false。\n"
                "2. 若含糊且画像能补 → 基于画像补全缺省(语言/技术栈/格式/范围/默认值), "
                "严格保留用户原意, 不得臆造用户没表达的新需求。\n"
                "3. refined 应是 '用户本来就想问的更精确版本', 而不是你对问题的回答。\n"
                f"\n[用户画像]\n" + "\n".join(profile_lines) +
                f"\n\n[用户的问题]\n{task}\n"
                '\n只输出 JSON(不要解释): '
                '{"needs_refine": true/false, "refined": "改写后的问题(needs_refine=false 时留空)", '
                '"reason": "一句话说明补了什么(中文)"}'
            )
        try:
            raw = llm(prompt)
        except Exception as e:
            self.logger.warning(f"[refine] LLM 调用异常 (用原问题继续): trace_id={trace_id} err={e}")
            return task, None

        data = self._parse_reflection_json(raw)  # 复用通用 LLM-JSON 解析 (兼容围栏/前后文字)
        if not isinstance(data, dict):
            return task, None

        if is_ask:
            if not data.get("needs_clarify"):
                return task, None
            question = str(data.get("clarify_question") or "").strip()
            # 安全阀: 澄清问题须非空且像个问题 (太短多半是 LLM 抽风)
            if len(question) < 4:
                return task, None
            meta = {
                "action": "clarify",
                "original": task[:300],
                "question": question[:300],
                "reason": str(data.get("reason") or "")[:200],
            }
            self.logger.info(
                f"[refine] ask 模式判定需澄清: trace_id={trace_id} reason={meta['reason']!r}"
            )
            return task, meta  # task 不改, execute 见 action=clarify 早退反问

        if not data.get("needs_refine"):
            return task, None
        refined = str(data.get("refined") or "").strip()
        # 安全阀: 改写结果须非空, 且不能比原问题短太多 (防 LLM 把问题"截没了"/答非所问)
        if not refined or len(refined) < max(4, len(task) // 4):
            return task, None
        meta = {
            "action": "rewrite",
            "original": task[:300],
            "refined": refined[:300],
            "reason": str(data.get("reason") or "")[:200],
        }
        self.logger.info(
            f"[refine] 问题已基于画像优化: trace_id={trace_id} reason={meta['reason']!r}"
        )
        return refined, meta

    def _extract_and_store_memory(
        self,
        task: str,
        final_answer: str,
        session_id: str,
        tenant_id: str,
        trace_id: Optional[str],
    ) -> int:
        """从 (task, final_answer) 抽 fact list 入库 long_term_memory.

        返回新增 / bump 的 fact 总数. 用户的设计核心:
            extract → 每条 add_fact → impl 内部按 hash + 语义双阶段 dedup
            重复 fact 走 mark_accessed 而不是新增 (符合"存在就标记")
        失败 (LLM 不可用 / 抽取报错) 不阻断主响应.
        """
        try:
            facts = self.long_term_memory.extract_facts(
                messages=[
                    {"role": "user", "content": task},
                    {"role": "assistant", "content": final_answer},
                ],
                tenant_id=tenant_id,
                session_id=session_id,
            )
        except NotImplementedError:
            # impl 没注入 llm_client — 静默关闭, 不报警
            return 0
        except Exception as e:
            self.logger.warning(
                f"[memory] extract_facts 失败: tenant={tenant_id} trace_id={trace_id} err={e}"
            )
            return 0

        count = 0
        for f in facts:
            try:
                self.long_term_memory.add_fact(f)
                count += 1
            except Exception as e:
                self.logger.warning(
                    f"[memory] add_fact 失败 fact_id={f.fact_id}: {e}"
                )
        if count:
            self.logger.info(
                f"[memory] 落盘 {count} 条 fact: tenant={tenant_id} session={session_id} "
                f"trace_id={trace_id}"
            )
        return count
