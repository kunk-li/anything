# -*- coding: utf-8 -*-
"""
ReflectionMixin (从 impl.py 拆出 — Task III #95 反思环, 零行为变更)

Reflexion / Self-critique 风格的 critique → revise 闭环:
    _reflect_revise        对初步答案做 critique → revise (2 步 LLM); 失败保留原答案
    _parse_reflection_json LLM 返回 JSON 解析 (兼容 markdown 围栏 / 前后解释文字)

注: _parse_reflection_json 也被 MemoryMixin 当通用 LLM-JSON 解析复用 (经 self 解析, MRO 提供)。

依赖 SimpleAgent (self) 字段/方法 (由 __init__ 及其它 mixin / impl 提供):
    _resolve_llm_planner (impl)
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional


class ReflectionMixin:
    """Reflection 反思环 (Reflexion / Self-critique): critique → revise。"""

    def _reflect_revise(
        self, task: str, initial_answer: str, trace_id: Optional[str],
    ) -> tuple:
        """对初步答案做 critique → revise. 类 Reflexion 论文 / OpenAI o1 思路.

        2 步 LLM 调用:
          1. critique: "Given the task and initial answer, identify any flaws,
             missing info, or improvements."
          2. revise: "Given the critique, produce an improved answer."

        失败 (LLM 不可用 / critique 解析失败) → 返 (None, meta), 主路径保留原答案.
        meta: {critique_text, n_issues, llm_calls, cost_ms}.
        """
        llm = self._resolve_llm_planner(trace_id=trace_id)
        if llm is None:
            return None, {"skipped": "no_llm"}

        meta: Dict[str, Any] = {"llm_calls": 0}
        t0 = time.time()

        # ----- 1. Critique -----
        critique_prompt = (
            "你是严格的答案评审. 给定任务和初步答案, 找出缺陷 / 缺失 / 模糊点 / 可改进处.\n\n"
            f"[任务]\n{task}\n\n"
            f"[初步答案]\n{initial_answer}\n\n"
            "请返回 JSON: {\n"
            '  "issues": ["缺陷1", "缺陷2", ...],\n'
            '  "missing_info": ["缺哪些上下文", ...],\n'
            '  "overall_quality": 1-5,\n'
            '  "should_revise": true|false\n'
            "}\n"
            "只返回 JSON, 别加任何解释."
        )
        try:
            critique_raw = llm(critique_prompt)
            meta["llm_calls"] += 1
        except Exception as e:
            return None, {"skipped": "critique_llm_failed", "err": str(e)}

        critique = self._parse_reflection_json(critique_raw or "")
        if not critique:
            return None, {"skipped": "critique_json_parse_failed", "raw": (critique_raw or "")[:200]}

        meta["critique"] = critique
        meta["n_issues"] = len(critique.get("issues") or [])
        meta["overall_quality"] = critique.get("overall_quality")

        # 自评分高 + LLM 自己说不用 revise → 跳过, 节省一轮调用
        if not critique.get("should_revise", True) and int(critique.get("overall_quality") or 0) >= 4:
            meta["skipped_revise"] = "self_eval_good"
            meta["cost_ms"] = int((time.time() - t0) * 1000)
            return None, meta

        # ----- 2. Revise -----
        issues_str = "\n".join(f"- {x}" for x in (critique.get("issues") or [])[:5])
        missing_str = "\n".join(f"- {x}" for x in (critique.get("missing_info") or [])[:5])
        revise_prompt = (
            "你需要根据评审意见把答案修订得更好.\n\n"
            f"[任务]\n{task}\n\n"
            f"[初步答案]\n{initial_answer}\n\n"
            f"[发现的问题]\n{issues_str or '(无)'}\n\n"
            f"[缺失信息]\n{missing_str or '(无)'}\n\n"
            "请直接返回修订后的最终答案 (不要解释为什么修订, 不加 '修订后:' 等前缀)."
        )
        try:
            revised = llm(revise_prompt)
            meta["llm_calls"] += 1
        except Exception as e:
            return None, {**meta, "skipped": "revise_llm_failed", "err": str(e)}

        if not revised or not str(revised).strip():
            return None, {**meta, "skipped": "revise_empty"}

        meta["cost_ms"] = int((time.time() - t0) * 1000)
        return str(revised).strip(), meta

    @staticmethod
    def _parse_reflection_json(raw: str) -> Optional[Dict[str, Any]]:
        """LLM 返 JSON dict, 同 long_term_memory 的 _parse_extracted_json 兼容
        markdown 围栏 / 前后解释文字."""
        import json as _json
        import re as _re

        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = _re.sub(r"^```[a-zA-Z]*\n", "", raw)
            raw = _re.sub(r"\n```\s*$", "", raw)
            raw = raw.strip()
        try:
            obj = _json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        m = _re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            obj = _json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
