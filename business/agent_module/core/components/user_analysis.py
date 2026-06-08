# -*- coding: utf-8 -*-
"""
用户分析流程 (analyze_user) — 补缺失的"主动分析使用者"闭环。

对称于 self_reflection (分析 agent 自己的行为): 这里**分析使用者**。区别于现有"被动碎片":
  - _extract_and_store_memory: 每轮收尾被动抽几条 fact (碎片);
  - get_user_profile: 把 fact 按 5 维度分桶聚合 (无分析)。
本模块 = 主动闭环: 交互历史 → LLM **提炼** 用户的工作流程/做事习惯/反复需求/领域/薄弱点 →
  ① insights: 只读"用户洞察"总览;
  ② proposals: 可写回画像的增强提议 (dim+content, dry-run, 须人审批才反哺画像)。

零外部依赖 (facts / llm_call 由接入层注入), 可独立单测。fail-open: 分析出错绝不阻断 (返回空),
它是增强不是新失败源。接入: SelfMaintenanceMixin.analyze_user / apply_user_insights。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

# 画像 5 维度 (与 long_term_memory.get_user_profile 一致); 增强提议只能写这些维度
PROFILE_DIMS = ("preference", "style", "convention", "domain", "weakness")

LLMCall = Callable[[str], str]


@dataclass
class UserInsightProposal:
    """一条画像增强提议 (建议性, 须人审批才写回画像)。"""
    id: str
    dim: str                # preference|style|convention|domain|weakness
    content: str            # 可写回画像的一句话
    reason: str = ""        # 依据

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "dim": self.dim, "content": self.content, "reason": self.reason}


def aggregate_user_signals(facts: List[Any], max_samples: int = 40) -> Dict[str, Any]:
    """确定性聚合用户长期记忆 (无 LLM): 按 content_type 计数 + 采样片段。供 Inspector 分析 / 前端 debug。"""
    by_type: Dict[str, int] = {}
    samples: List[Dict[str, Any]] = []
    for f in facts or []:
        ct = getattr(f, "content_type", None) or "other"
        by_type[ct] = by_type.get(ct, 0) + 1
        if len(samples) < max_samples:
            samples.append({
                "content": (getattr(f, "content", "") or "")[:160],
                "type": ct,
                "tags": list(getattr(f, "tags", []) or []),
            })
    return {"total_facts": len(facts or []), "by_type": by_type, "samples": samples}


class UserAnalysisInspector:
    """把用户信号交给 LLM, 提炼洞察 + 画像增强提议 (dry-run, 不改任何状态)。

    fail-open: 无信号 / LLM 不可用 / 输出无法解析 → 返回 {insights:[], proposals:[]}。绝不抛。
    """

    def __init__(self, llm_call: LLMCall):
        self.llm_call = llm_call

    def analyze(self, signals: Dict[str, Any], context: str = "") -> Dict[str, Any]:
        if not signals or not signals.get("total_facts"):
            return {"insights": [], "proposals": []}
        samples_txt = "\n".join(
            f"- [{s.get('type')}] {s.get('content')}" for s in signals.get("samples", [])
        )
        prompt = (
            "你在帮一个 AI 助手'分析它的使用者', 目标是更懂这个人、更好地协助。\n"
            "下面是这个用户长期记忆里的片段(按类型标注)。请提炼:\n"
            "1. 典型工作流程 / 做事习惯; 2. 反复出现的需求; 3. 擅长领域; 4. 需 agent 主动补位的薄弱点。\n"
            "规则: 只基于给的片段, 不臆造用户没体现的东西; 简洁、具体、第二人称或客观陈述。\n"
            "只输出 JSON(不要解释): "
            '{"insights": ["一句话洞察", ...], '
            '"proposals": [{"dim": "preference|style|convention|domain|weakness", '
            '"content": "可写回画像的一句话(该维度)", "reason": "依据(中文)"}]}\n'
            f"\n[用户记忆片段]\n{samples_txt}\n"
            + (f"\n[补充上下文]\n{context}\n" if context else "")
        )
        try:
            raw = self.llm_call(prompt)
        except Exception:
            return {"insights": [], "proposals": []}
        obj = self._parse_json_object(raw)
        insights = [str(x).strip() for x in (obj.get("insights") or []) if str(x).strip()][:10]
        proposals: List[UserInsightProposal] = []
        for i, it in enumerate(obj.get("proposals") or []):
            if not isinstance(it, dict):
                continue
            dim = str(it.get("dim") or "").strip().lower()
            content = str(it.get("content") or "").strip()
            if dim not in PROFILE_DIMS or not content:
                continue
            proposals.append(UserInsightProposal(
                id=f"ua_{i}", dim=dim, content=content[:200],
                reason=str(it.get("reason") or "")[:200],
            ))
        return {"insights": insights, "proposals": proposals}

    @staticmethod
    def _parse_json_object(raw: str) -> Dict[str, Any]:
        """LLM 输出 JSON 对象, 兼容 markdown 围栏 / 前后解释文字。失败返 {}。"""
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"```\s*$", "", raw).strip()
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            pass
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return {}
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
