# -*- coding: utf-8 -*-
"""
SkillDistillMixin (从 impl.py 拆出 — #1 技能自动沉淀, 零行为变更)

借鉴 Hermes 学习闭环: 成功复杂任务 → 后台提炼一条可复用 skill / 抽 fact 入长期记忆。
全程 fail-open, 默认关, 不阻塞主流程:
    _distill_skill_async   成功复杂任务收尾时后台线程沉淀 skill
    _extract_memory_async  流式收尾后台抽 fact 入长期记忆 (补齐流式路径的画像学习)
    _distill_skill         LLM 从一次成功任务提炼 skill 并写库 (去重)

依赖 SimpleAgent (self) 字段/方法 (由 __init__ 及其它 mixin / impl 提供):
    enable_skill_distill, skill_distill_min_tools, memory_enabled, long_term_memory,
    logger, _resolve_llm_planner (impl), _extract_and_store_memory (MemoryMixin)
"""
from __future__ import annotations


class SkillDistillMixin:
    """成功任务 → 后台沉淀可复用 skill / 抽取长期记忆 (fail-open, 默认关)。"""

    def _distill_skill_async(self, task, tool_results, final_answer, trace_id=None):
        """成功复杂任务收尾时, 后台线程沉淀 skill (不阻塞主流程 / 不延迟 done)。默认关。"""
        try:
            if not getattr(self, "enable_skill_distill", False):
                return
            n_tools = len([tr for tr in (tool_results or []) if tr and tr.get("tool_name")])
            if n_tools < int(getattr(self, "skill_distill_min_tools", 2)):
                return  # 简单任务不沉淀
            if not (final_answer and str(final_answer).strip()):
                return
            import threading
            threading.Thread(
                target=self._distill_skill,
                args=(task, tool_results, final_answer, trace_id),
                daemon=True,
            ).start()
        except Exception:
            pass  # fail-open: 沉淀永不影响主流程

    def _extract_memory_async(self, task, final_answer, session_id, tenant_id, trace_id=None):
        """流式收尾后台抽 fact 入长期记忆 (不阻塞 done)。同步 execute 是 inline 抽 (impl.py:571);
        流式路径此前完全不抽 → "越用越懂你/使用者画像" 在网页(流式)路径不生效, 这里补齐。
        async (daemon 线程) 跑, 与 _distill_skill_async 同样不增 done 延迟。全程 fail-open。"""
        try:
            if not (getattr(self, "memory_enabled", False)
                    and getattr(self, "long_term_memory", None) is not None):
                return
            if not (task and final_answer and str(final_answer).strip()):
                return
            import threading
            threading.Thread(
                target=self._extract_and_store_memory,
                args=(task, final_answer, session_id, tenant_id, trace_id),
                daemon=True,
            ).start()
        except Exception:
            pass  # fail-open: 抽取永不影响主流程

    def _distill_skill(self, task, tool_results, final_answer, trace_id=None):
        """LLM 从一次成功任务提炼一条可复用 skill 并写入 skill 库。去重 + 全程 fail-open。"""
        try:
            llm_call = self._resolve_llm_planner(trace_id=trace_id)
            if llm_call is None:
                return None
            tools_used = [tr.get("tool_name") for tr in (tool_results or [])
                          if tr and tr.get("tool_name")]
            prompt = (
                "下面是一次已成功完成的任务。请把它提炼成一条【可复用技能(skill)】, 供以后遇到\n"
                "同类任务时直接复用 (作为提示注入)。要通用、可迁移, 不要带这次的具体数据。\n"
                f"【任务】{str(task)[:500]}\n"
                f"【用到的工具】{tools_used}\n"
                f"【最终结果(节选)】{str(final_answer)[:500]}\n\n"
                "只输出 JSON, 不要任何其他文字:\n"
                '{"name": "蛇形小写英文名", "description": "一句话描述", '
                '"triggers": ["会触发这类任务的关键词(3-6个,中英文皆可)"], '
                '"tools": ["用到的工具名"], '
                '"body": "遇到这类任务用什么步骤/工具/注意点去做的简明指南(中文,100-300字)"}'
            )
            raw = llm_call(prompt) or ""
            import json as _json
            import re as _re
            m = _re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return None
            data = _json.loads(m.group(0))
            name = str(data.get("name") or "").strip()
            triggers = [str(t).strip() for t in (data.get("triggers") or []) if str(t).strip()]
            body = str(data.get("body") or "").strip()
            if not (name and triggers and body):
                return None
            from skills_module.impl import Skill, get_skill_registry
            reg = get_skill_registry()
            if reg.find_by_triggers(triggers) is not None:
                self.logger.info(f"[skill-distill] 已有同类 skill, 跳过: name={name}")
                return None  # 去重: 已有高度重叠的 skill (v1 不合并)
            skill = Skill(
                name=name,
                description=str(data.get("description") or task)[:120],
                triggers=triggers,
                tools=[str(t) for t in (data.get("tools") or tools_used) if t],
                priority=1,
                body=body,
            )
            path = reg.save_skill(skill, source="auto")
            if path:
                self.logger.info(
                    f"[skill-distill] 沉淀新技能: name={name} triggers={triggers} → {path}")
            return path
        except Exception as e:
            self.logger.warning(f"[skill-distill] 沉淀失败 (忽略): {e}")
            return None
