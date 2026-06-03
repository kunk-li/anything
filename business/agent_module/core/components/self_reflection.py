# -*- coding: utf-8 -*-
"""
自主维护 (方向4 / 第一级: 建议性自主) — 两个域: 行为自反思 + 记忆健康自维护.

设计 (务必分级, 全程 human-in-loop):
    THINK    只读检视 agent 自身历史执行 (审计日志聚合), 推断哪些行为问题反复出现
    PROPOSE  LLM 元级反思 → 结构化改进提议 (问题 + 证据 + 建议动作), dry-run 零改动
    APPROVE  每条提议须人显式审批 (由接入层 gate)
    APPLY    仅审批后经安全动作执行 (MVP: 把"教训"落长期记忆反哺画像; 绝不自动改配置/代码)

区别于 verifier.py 的 TaskVerifier / impl 的 _reflect_revise (对单次答案反思): 本模块是
**元级**反思 —— 跨多次任务的行为模式 (哪个工具老失败 / 反复超时 / 成本异常 / 错误码扎堆)。

零外部依赖 (records / llm_call 由接入层注入), 可独立单测。fail-open: 反思本身出错
绝不阻断 (返回空提议), 它是增强不是新失败源。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

ACTION_TYPES = ("record_lesson", "config_suggestion", "investigate", "advisory")
SEVERITIES = ("info", "warn", "high")


@dataclass
class ReflectionProposal:
    """一条行为改进提议 (建议性, 须人审批才执行)。"""
    id: str
    problem: str                       # 反复出现的问题
    evidence: str = ""                 # 支撑证据 (来自聚合信号)
    proposed_action: str = ""          # 建议怎么改
    action_type: str = "advisory"      # record_lesson | config_suggestion | investigate | advisory
    severity: str = "info"             # info | warn | high

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "problem": self.problem, "evidence": self.evidence,
            "proposed_action": self.proposed_action, "action_type": self.action_type,
            "severity": self.severity,
        }


# 接入层注入: prompt -> text (复用 agent 的 llm planner 通道)
LLMCall = Callable[[str], str]


def aggregate_audit_signals(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """确定性聚合审计记录 (无 LLM): per-tool 成败/错误码、llm 调用与成本。

    records: 审计日志 JSONL 反序列化的 dict 列表 (event=tool_call_finished/llm_call_finished/...)。
    返回结构化 signals 供 reflect() 反思, 也可直接给前端 / debug 看。
    """
    tool_stats: Dict[str, Dict[str, Any]] = {}
    llm_calls = 0
    total_cost = 0.0
    tool_finished = 0
    tool_failed = 0
    for r in records:
        if not isinstance(r, dict):
            continue
        ev = r.get("event")
        if ev == "tool_call_finished":
            tool = str(r.get("tool") or "?")
            st = tool_stats.setdefault(tool, {"calls": 0, "failures": 0, "error_codes": {}})
            st["calls"] += 1
            tool_finished += 1
            if not r.get("success"):
                st["failures"] += 1
                tool_failed += 1
                code = str(r.get("code") or "UNKNOWN")
                st["error_codes"][code] = st["error_codes"].get(code, 0) + 1
        elif ev == "llm_call_finished":
            llm_calls += 1
            try:
                total_cost += float(r.get("cost_usd") or 0.0)
            except (TypeError, ValueError):
                pass
    # 派生: 每工具失败率 + 排序 (失败率高 & 调用多 优先)
    tools: List[Dict[str, Any]] = []
    for name, st in tool_stats.items():
        calls = st["calls"]
        fr = round(st["failures"] / calls, 3) if calls else 0.0
        tools.append({
            "tool": name, "calls": calls, "failures": st["failures"],
            "failure_rate": fr, "error_codes": st["error_codes"],
        })
    tools.sort(key=lambda t: (t["failure_rate"], t["failures"]), reverse=True)
    return {
        "records_analyzed": len(records),
        "tool_calls_finished": tool_finished,
        "tool_failures": tool_failed,
        "llm_calls": llm_calls,
        "total_cost_usd": round(total_cost, 4),
        "tools": tools,
    }


class SelfReflectionInspector:
    """元级自反思: 把聚合信号交给 LLM, 产出结构化改进提议 (dry-run, 不改任何状态)。

    fail-open: 无信号 / LLM 不可用 / 输出无法解析 → 返回 []。绝不抛、绝不改状态。
    """

    def __init__(self, llm_call: LLMCall):
        self.llm_call = llm_call

    def reflect(self, signals: Dict[str, Any], context: str = "") -> List[ReflectionProposal]:
        # 无信号可反思 (审计日志为空等) → 空
        if not signals or not signals.get("records_analyzed"):
            return []
        prompt = (
            "你在帮一个 AI agent 做'行为自反思': 下面是它最近一段执行的聚合统计(来自审计日志)。\n"
            "找出【反复出现、值得改进】的行为问题, 给出可执行的改进提议。规则:\n"
            "- 只提有数据支撑的问题(如某工具失败率高、成本异常、某错误码扎堆), 不要泛泛而谈;\n"
            "- action_type: record_lesson(沉淀为长期教训让以后注意) / config_suggestion(建议调配置) "
            "/ investigate(需人排查) / advisory(一般建议);\n"
            "- 没有值得提的 → 返回空数组 []。\n\n"
            f"【聚合统计】\n{json.dumps(signals, ensure_ascii=False)}\n"
            + (f"\n【补充上下文】\n{context}\n" if context else "")
            + '\n只输出 JSON 数组(不要其他文字), 每条: '
            '{"problem":"问题","evidence":"证据","proposed_action":"建议动作",'
            '"action_type":"record_lesson|config_suggestion|investigate|advisory","severity":"info|warn|high"}'
        )
        try:
            raw = self.llm_call(prompt) or ""
        except Exception:
            return []
        proposals: List[ReflectionProposal] = []
        for i, it in enumerate(self._parse_json_array(raw)):
            if not isinstance(it, dict):
                continue
            problem = str(it.get("problem") or "").strip()
            if not problem:
                continue
            atype = str(it.get("action_type") or "advisory").strip()
            atype = atype if atype in ACTION_TYPES else "advisory"
            sev = str(it.get("severity") or "info").strip()
            sev = sev if sev in SEVERITIES else "info"
            proposals.append(ReflectionProposal(
                id=f"rp{i + 1}",
                problem=problem[:300],
                evidence=str(it.get("evidence") or "")[:300],
                proposed_action=str(it.get("proposed_action") or "")[:300],
                action_type=atype,
                severity=sev,
            ))
        return proposals

    @staticmethod
    def _parse_json_array(raw: str) -> List[Any]:
        """LLM 输出 JSON 数组, 兼容 markdown 围栏 / 前后解释文字。"""
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n", "", raw)
            raw = re.sub(r"\n```\s*$", "", raw).strip()
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, list) else []
        except Exception:
            pass
        m = re.search(r"\[[\s\S]*\]", raw)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, list) else []
        except Exception:
            return []


# ============================================================
# 记忆健康域 (方向4 扩域): 只读检视长期记忆 → 确定性维护提议 (不调 LLM)
# ============================================================

# 须与 LongTermMemoryImpl.degrade_stale_refinable 的降级哨兵一致
_DEGRADED_SENTINEL = "[原始已清理, 仅保留精炼摘要]"
_CONSOLIDATE_MIN_REFINABLE = 5


def aggregate_memory_signals(
    facts: List[Any],
    now_ts: float,
    max_age_days: int = 90,
    min_access_count: int = 1,
) -> Dict[str, Any]:
    """只读统计长期记忆健康。facts 为 Fact-like 对象列表 (duck-typed: superseded_by /
    mutability / pinned / last_accessed / access_count / digest / content)。

    prune/degrade 候选谓词须与 LongTermMemoryImpl.prune_stale / degrade_stale_refinable 一致
    (变更那两处时同步此处)。不改任何状态。
    """
    cutoff = now_ts - max_age_days * 86400.0
    total = len(facts)
    active = superseded = refinable_active = canonical = 0
    prune_candidates = degrade_candidates = 0
    for f in facts:
        is_active = getattr(f, "superseded_by", None) is None
        active += 1 if is_active else 0
        superseded += 0 if is_active else 1
        mut = getattr(f, "mutability", "refinable")
        pinned = bool(getattr(f, "pinned", False))
        if mut == "canonical":
            canonical += 1
        if is_active and mut == "refinable":
            refinable_active += 1
        is_old = float(getattr(f, "last_accessed", 0.0) or 0.0) < cutoff
        ac = int(getattr(f, "access_count", 0) or 0)
        not_protected = (not pinned) and mut != "canonical"
        # prune_stale 谓词: 非 pinned, 非 canonical, 老, access < min
        if not_protected and is_old and ac < min_access_count:
            prune_candidates += 1
        # degrade_stale_refinable 谓词: 非 pinned, 非 canonical, 有 digest, 未降级过, 老
        digest = getattr(f, "digest", "") or ""
        content = getattr(f, "content", "") or ""
        if not_protected and digest and content != _DEGRADED_SENTINEL and is_old:
            degrade_candidates += 1
    return {
        "total": total, "active": active, "superseded": superseded,
        "refinable_active": refinable_active, "canonical": canonical,
        "prune_candidates": prune_candidates, "degrade_candidates": degrade_candidates,
        "max_age_days": max_age_days, "min_access_count": min_access_count,
    }


def propose_from_memory_signals(signals: Dict[str, Any]) -> List[ReflectionProposal]:
    """确定性规则: 记忆健康统计 → 维护提议 (dry-run, 不调 LLM)。
    action_type 映射现成算子: run_prune / run_degrade / run_reconcile / run_consolidate。"""
    props: List[ReflectionProposal] = []
    age = signals.get("max_age_days", 90)
    n_prune = signals.get("prune_candidates", 0)
    if n_prune > 0:
        props.append(ReflectionProposal(
            id=f"mm{len(props) + 1}", problem=f"{n_prune} 条陈旧 fact (>{age}天 且 低访问)",
            evidence=f"prune_candidates={n_prune}",
            proposed_action="运行 prune_stale 清理这些陈旧 fact",
            action_type="run_prune", severity="info",
        ))
    n_deg = signals.get("degrade_candidates", 0)
    if n_deg > 0:
        props.append(ReflectionProposal(
            id=f"mm{len(props) + 1}", problem=f"{n_deg} 条陈旧但有精炼的 fact 可降级 (丢原始留 digest)",
            evidence=f"degrade_candidates={n_deg}",
            proposed_action="运行 degrade_stale_refinable 释放原始内容",
            action_type="run_degrade", severity="info",
        ))
    n_ref = signals.get("refinable_active", 0)
    if n_ref >= 2:
        props.append(ReflectionProposal(
            id=f"mm{len(props) + 1}", problem=f"{n_ref} 条有效可提炼偏好, 可能存在对立 (需 LLM 检测)",
            evidence=f"refinable_active={n_ref}",
            proposed_action="运行 reconcile_conflicts 消解对立偏好",
            action_type="run_reconcile", severity="info",
        ))
    if n_ref >= _CONSOLIDATE_MIN_REFINABLE:
        props.append(ReflectionProposal(
            id=f"mm{len(props) + 1}", problem=f"{n_ref} 条可提炼偏好, 可能有重复可归纳",
            evidence=f"refinable_active={n_ref}",
            proposed_action="运行 consolidate 归纳相关/重复条目",
            action_type="run_consolidate", severity="info",
        ))
    return props
