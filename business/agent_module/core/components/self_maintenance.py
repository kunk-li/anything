# -*- coding: utf-8 -*-
"""
SelfMaintenanceMixin (优化③ 从 impl.py 拆出 — 方向4 建议性自主维护)

把"自主维护/建议性自主"一整组从 SimpleAgent god class 抽到此 mixin (零行为变更):
    _resolve_audit_path / _read_audit_tail   审计日志定位 + 尾读
    self_reflect / apply_reflection_proposals 行为自反思 (审计聚合→LLM 元级反思→人审批落记忆)
    propose_memory_maintenance / apply_memory_maintenance  记忆健康检视 + 审批后映射现成算子
    propose_code_doc_maintenance             代码/文档 advisory 清单 (不自动改)
    run_maintenance_scan / _notify_maintenance  聚合三域提议 + 审计通知 + 预授权安全自动

全程 human-in-loop / 默认关 (enable_self_reflection)。详见各方法 docstring 与 HANDOFF §5。

依赖 SimpleAgent (self) 字段/方法 (由 __init__ 及其它 mixin / impl 提供):
    enable_self_reflection, long_term_memory, logger, auto_approve_maintenance, deps,
    _resolve_llm_planner (impl), _resolve_project_root (impl, 位置相关故留在 impl)
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from .self_reflection import (
    SelfReflectionInspector, aggregate_audit_signals,
    aggregate_memory_signals, propose_from_memory_signals,
    scan_code_doc_health, propose_from_code_doc_signals,
)
from .user_analysis import aggregate_user_signals, UserAnalysisInspector, PROFILE_DIMS


class SelfMaintenanceMixin:
    """方向4 自主维护 (建议性自主全档): 行为自反思 / 记忆健康 / 代码文档 / 定时扫描通知。"""

    def _resolve_audit_path(self, audit_path: Optional[str]) -> Optional[str]:
        """定位审计日志 JSONL: 显式参数 > deps.audit_logger.path > 全局单例 > env。"""
        if audit_path:
            return audit_path
        al = getattr(getattr(self, "deps", None), "audit_logger", None)
        if al is not None and getattr(al, "path", None):
            return str(al.path)
        try:
            from audit_module import get_audit_logger
            return str(get_audit_logger().path)
        except Exception:
            return os.environ.get("ANYTHING_AUDIT_LOG_PATH")

    def _read_audit_tail(self, path: str, max_records: int) -> List[Dict[str, Any]]:
        """读审计日志尾部最多 max_records 条 (JSONL); 坏行跳过, 文件缺失/异常返 []。"""
        import json as _json
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, ValueError):
            return []
        out: List[Dict[str, Any]] = []
        for line in lines[-max(1, max_records):]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out

    def self_reflect(
        self,
        audit_path: Optional[str] = None,
        max_records: int = 500,
        trace_id: Optional[str] = None,
        context: str = "",
    ) -> Dict[str, Any]:
        """方向4 第一级: 按需检视自身历史执行 (审计日志聚合) → LLM 元级反思 → 改进提议。

        **dry-run 零改动** (不碰记忆/配置/代码); 提议须经 apply_reflection_proposals 人审批后才落地。
        返回 {"enabled","signals","proposals":[...],"note"}。enable_self_reflection=off /
        无 LLM 通道 / 审计日志为空 → 空提议 + note (fail-safe, 绝不抛、绝不改状态)。
        """
        if not self.enable_self_reflection:
            return {"enabled": False, "signals": {}, "proposals": [],
                    "note": "self_reflection 未启用 (开: agent.enable_self_reflection)"}
        path = self._resolve_audit_path(audit_path)
        records = self._read_audit_tail(path, max_records) if path else []
        signals = aggregate_audit_signals(records)
        llm = self._resolve_llm_planner(trace_id=trace_id)
        if llm is None:
            return {"enabled": True, "signals": signals, "proposals": [],
                    "note": "无 LLM 通道, 只给聚合信号不反思"}
        try:
            proposals = SelfReflectionInspector(llm_call=llm).reflect(signals, context=context)
        except Exception as e:
            self.logger.warning(f"[self_reflect] 反思失败 (返回空提议): trace_id={trace_id} err={e}")
            proposals = []
        self.logger.info(
            f"[self_reflect] 分析 {signals.get('records_analyzed', 0)} 条审计 → "
            f"{len(proposals)} 条提议: trace_id={trace_id}"
        )
        return {
            "enabled": True, "signals": signals,
            "proposals": [p.to_dict() for p in proposals],
            "note": "dry-run 提议; 审批后调 apply_reflection_proposals 落地",
        }

    def apply_reflection_proposals(
        self,
        proposals: List[Dict[str, Any]],
        approved_ids: List[str],
        tenant_id: str = "default",
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """方向4: 仅把【人审批通过】且 action_type=record_lesson 的提议落长期记忆 (操作约定)。
        非 record_lesson / 未审批 一律不动; 绝不自动改配置/代码。返回 {"applied","skipped","details"}。

        建议性自主闭环: 反思 → 提议 → 人审批 → 教训以 convention 进记忆 → 经画像 always-on
        注入改进未来行为。tags=['self_reflection','lesson'] 便于溯源/后续清理。
        """
        approved = set(approved_ids or [])
        applied, skipped = 0, 0
        details: List[Dict[str, Any]] = []
        for p in (proposals or []):
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            if pid not in approved:
                skipped += 1
                continue
            if p.get("action_type") != "record_lesson":
                skipped += 1
                details.append({"id": pid, "result": "skip_non_lesson",
                                "action_type": p.get("action_type")})
                continue
            if self.long_term_memory is None:
                skipped += 1
                details.append({"id": pid, "result": "skip_no_memory"})
                continue
            lesson = str(p.get("proposed_action") or p.get("problem") or "").strip()
            if not lesson:
                skipped += 1
                continue
            try:
                from long_term_memory_module import Fact
                f = Fact.make(
                    content=f"[自反思·约定] {lesson}",
                    tenant_id=tenant_id, mutability="refinable",
                    content_type="convention", digest=lesson[:100],
                    tags=["self_reflection", "lesson"],
                )
                self.long_term_memory.add_fact(f)
                applied += 1
                details.append({"id": pid, "result": "recorded_to_memory", "fact_id": f.fact_id})
            except Exception as e:
                skipped += 1
                details.append({"id": pid, "result": f"error: {e}"})
        self.logger.info(
            f"[self_reflect] apply: {applied} 落记忆 / {skipped} 跳过: trace_id={trace_id}"
        )
        return {"applied": applied, "skipped": skipped, "details": details}

    def propose_memory_maintenance(
        self,
        tenant_id: str = "default",
        max_age_days: int = 90,
        min_access_count: int = 1,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """方向4 扩域·记忆域: 只读检视长期记忆健康 → 确定性维护提议 (dry-run 零改动)。

        提议须经 apply_memory_maintenance 人审批后才执行 (复用现成算子)。区别于行为自反思
        (self_reflect, 审计日志 + LLM): 记忆健康是**确定性**的 (计数 → 算子), 不调 LLM。
        gate enable_self_reflection; 无 long_term_memory → 空提议 (fail-safe, 绝不抛/改)。
        """
        if not self.enable_self_reflection:
            return {"enabled": False, "signals": {}, "proposals": [],
                    "note": "self_reflection 未启用 (开: agent.enable_self_reflection)"}
        if self.long_term_memory is None:
            return {"enabled": True, "signals": {}, "proposals": [], "note": "未接长期记忆"}
        try:
            facts = self.long_term_memory.list_facts(tenant_id, limit=500)
            signals = aggregate_memory_signals(facts, time.time(), max_age_days, min_access_count)
            proposals = propose_from_memory_signals(signals)
        except Exception as e:
            self.logger.warning(f"[mem_maint] 检视失败 (返回空): trace_id={trace_id} err={e}")
            return {"enabled": True, "signals": {}, "proposals": [], "note": f"检视异常: {e}"}
        self.logger.info(
            f"[mem_maint] tenant={tenant_id} → {len(proposals)} 条提议: trace_id={trace_id}"
        )
        return {"enabled": True, "signals": signals,
                "proposals": [p.to_dict() for p in proposals],
                "note": "dry-run 提议; 审批后调 apply_memory_maintenance 执行"}

    def apply_memory_maintenance(
        self,
        proposals: List[Dict[str, Any]],
        approved_ids: List[str],
        tenant_id: str = "default",
        max_age_days: int = 90,
        min_access_count: int = 1,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """方向4 扩域·记忆域: 仅把【人审批】的提议映射到现成维护算子执行。
        run_prune→prune_stale / run_degrade→degrade_stale_refinable /
        run_reconcile→reconcile_conflicts / run_consolidate→consolidate。
        未审批 / 未知 action_type 一律不动。返回各 op 的执行结果计数。
        """
        approved = set(approved_ids or [])
        ltm = self.long_term_memory
        if ltm is None:
            return {"applied": 0, "details": [{"result": "skip_no_memory"}]}
        op_map = {
            "run_prune": lambda: ltm.prune_stale(
                tenant_id, max_age_days=max_age_days, min_access_count=min_access_count),
            "run_degrade": lambda: ltm.degrade_stale_refinable(tenant_id, max_age_days=max_age_days),
            "run_reconcile": lambda: ltm.reconcile_conflicts(tenant_id),
            "run_consolidate": lambda: ltm.consolidate(tenant_id),
        }
        applied = 0
        details: List[Dict[str, Any]] = []
        for p in (proposals or []):
            if not isinstance(p, dict) or p.get("id") not in approved:
                continue
            at = p.get("action_type")
            op = op_map.get(at)
            if op is None:
                details.append({"id": p.get("id"), "result": f"skip_unknown_action:{at}"})
                continue
            try:
                n = op()
                details.append({"id": p.get("id"), "op": at, "result": n})
                applied += 1
            except Exception as e:
                details.append({"id": p.get("id"), "op": at, "result": f"error: {e}"})
        self.logger.info(f"[mem_maint] apply: {applied} ops 执行: trace_id={trace_id}")
        return {"applied": applied, "details": details}

    def propose_code_doc_maintenance(
        self, root: Optional[str] = None, trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """方向4 扩域·代码文档域: 只读扫描项目代码/文档健康 → advisory 维护清单 (dry-run)。

        **advisory-only**: 代码/文档改动**不自动执行**(绝不执行性自主), 仅产出供人处理的清单
        (故无配套 apply 方法)。gate enable_self_reflection; 无法定位项目根 → 空 (fail-safe)。
        """
        if not self.enable_self_reflection:
            return {"enabled": False, "signals": {}, "proposals": [],
                    "note": "self_reflection 未启用 (开: agent.enable_self_reflection)"}
        root = root or self._resolve_project_root()
        if not root or not os.path.isdir(root):
            return {"enabled": True, "signals": {}, "proposals": [], "note": "未定位到项目根"}
        try:
            signals = scan_code_doc_health(root)
            proposals = propose_from_code_doc_signals(signals)
        except Exception as e:
            self.logger.warning(f"[codedoc] 扫描失败 (返回空): trace_id={trace_id} err={e}")
            return {"enabled": True, "signals": {}, "proposals": [], "note": f"扫描异常: {e}"}
        self.logger.info(
            f"[codedoc] root={root} → {len(proposals)} 条 advisory 提议: trace_id={trace_id}"
        )
        return {"enabled": True, "signals": signals,
                "proposals": [p.to_dict() for p in proposals],
                "note": "advisory; 代码/文档改动须人工处理, 不自动执行"}

    def run_maintenance_scan(
        self,
        tenant_id: str = "default",
        root: Optional[str] = None,
        trace_id: Optional[str] = None,
        scope=("behavior", "memory", "code_doc"),
        auto_apply: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """方向4 定时提议+通知 (+更高自主档): 聚合各自维护域的提议(dry-run) + 写审计通知。
        可由 TaskScheduler 周期触发 (extra_params.maintenance_scan=true 的请求)。

        auto_apply: None=按 standing-approval 名单(`auto_approve_maintenance`)决定; True/False 强制。
        自动执行**仅限** (名单 ∩ 安全确定性算子 run_prune/run_degrade); 默认名单空=零自动=纯提议。
        reconcile/consolidate(LLM 不确定) 与 code_doc(advisory) **永不**自动。各域 fail-safe 互不影响。
        """
        if not self.enable_self_reflection:
            return {"enabled": False, "proposals": [], "note": "self_reflection 未启用"}
        all_proposals: List[Dict[str, Any]] = []
        by_domain: Dict[str, int] = {}
        runners = {
            "behavior": lambda: self.self_reflect(trace_id=trace_id),
            "memory": lambda: self.propose_memory_maintenance(tenant_id=tenant_id, trace_id=trace_id),
            "code_doc": lambda: self.propose_code_doc_maintenance(root=root, trace_id=trace_id),
        }
        for dom in scope:
            run = runners.get(dom)
            if run is None:
                continue
            try:
                props = run().get("proposals", []) or []
            except Exception as e:
                self.logger.warning(f"[maint_scan] 域 {dom} 检视失败 (跳过): trace_id={trace_id} err={e}")
                props = []
            by_domain[dom] = len(props)
            for p in props:
                all_proposals.append({**p, "domain": dom})
        self._notify_maintenance(all_proposals, by_domain, tenant_id, trace_id)
        result = {
            "enabled": True, "tenant_id": tenant_id,
            "total_proposals": len(all_proposals), "by_domain": by_domain,
            "proposals": all_proposals,
            "note": "dry-run 提议已通知(审计); 须人审批后调对应 apply_* 执行",
        }

        # 更高自主档: standing-approval 预授权自动执行 (默认名单空 → 跳过, 纯提议)
        do_auto = auto_apply if auto_apply is not None else bool(self.auto_approve_maintenance)
        if do_auto:
            # 安全天花板: 只有这两个确定性 memory 算子可自动; reconcile/consolidate/code_doc 永不自动
            eligible = self.auto_approve_maintenance & {"run_prune", "run_degrade"}
            mem_props = [p for p in all_proposals
                         if p.get("domain") == "memory" and p.get("action_type") in eligible]
            if mem_props:
                applied = self.apply_memory_maintenance(
                    mem_props, approved_ids=[p["id"] for p in mem_props],
                    tenant_id=tenant_id, trace_id=trace_id,
                )
                result["auto_applied"] = {"policy": sorted(eligible), **applied}
                result["note"] = (f"预授权自动执行 {applied.get('applied', 0)} 项(policy={sorted(eligible)}); "
                                  "其余仍须人审批")
                self.logger.info(
                    f"[maint_scan] 预授权自动执行 {applied.get('applied', 0)} 项: "
                    f"policy={sorted(eligible)} trace_id={trace_id}"
                )
        return result

    def _notify_maintenance(self, proposals, by_domain, tenant_id, trace_id) -> None:
        """通知渠道: 写一条 maintenance_scan 审计记录 (admin/运维可见)。失败静默, 不阻断。"""
        try:
            from datetime import datetime, timezone
            al = getattr(getattr(self, "deps", None), "audit_logger", None)
            if al is None:
                from audit_module import get_audit_logger
                al = get_audit_logger()
            al.write({
                "timestamp_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": "maintenance_scan",
                "tenant_id": tenant_id, "trace_id": trace_id,
                "proposal_count": len(proposals), "by_domain": by_domain,
            })
        except Exception:
            pass

    # ============================================================
    # 用户分析流程 (analyze_user): 主动分析使用者 — 对称于上面的 self_reflect(分析 agent 自己)
    # ============================================================
    def analyze_user(
        self, tenant_id: str = "default", trace_id: Optional[str] = None,
        max_facts: int = 200, context: str = "",
    ) -> Dict[str, Any]:
        """主动分析使用者: 长期记忆 → LLM 提炼 工作流程/习惯/反复需求/领域/薄弱点 →
        insights(只读总览) + proposals(画像增强, dry-run, 须 apply_user_insights 人审批反哺)。

        **默认关** (agent.enable_user_analysis); 全程 fail-open (出错返空, 绝不阻断)。
        区别于 get_user_profile(被动分桶聚合) — 这里是主动提炼"这个人怎么做事"。
        """
        if not getattr(self, "enable_user_analysis", False):
            return {"enabled": False, "insights": [], "proposals": [],
                    "note": "user_analysis 未启用 (开: agent.enable_user_analysis)"}
        if self.long_term_memory is None:
            return {"enabled": True, "insights": [], "proposals": [], "note": "未接长期记忆"}
        try:
            facts = self.long_term_memory.list_facts(tenant_id, limit=max_facts)
            signals = aggregate_user_signals(facts)
        except Exception as e:
            self.logger.warning(f"[user_analysis] 聚合失败 (返回空): trace_id={trace_id} err={e}")
            return {"enabled": True, "insights": [], "proposals": [], "note": f"聚合异常: {e}"}
        llm = self._resolve_llm_planner(trace_id=trace_id)
        if llm is None:
            return {"enabled": True, "insights": [], "proposals": [], "signals": signals,
                    "note": "无 LLM 通道, 只给聚合信号不分析"}
        try:
            result = UserAnalysisInspector(llm_call=llm).analyze(signals, context=context)
        except Exception as e:
            self.logger.warning(f"[user_analysis] 分析失败 (返回空): trace_id={trace_id} err={e}")
            result = {"insights": [], "proposals": []}
        self.logger.info(
            f"[user_analysis] tenant={tenant_id} → {len(result.get('insights', []))} 洞察 / "
            f"{len(result.get('proposals', []))} 画像提议: trace_id={trace_id}"
        )
        return {
            "enabled": True, "tenant_id": tenant_id,
            "insights": result.get("insights", []),
            "proposals": [p.to_dict() for p in result.get("proposals", [])],
            "note": "dry-run: insights 只读; proposals 须 apply_user_insights 审批后反哺画像",
        }

    def apply_user_insights(
        self, proposals: List[Dict[str, Any]], approved_ids: List[str],
        tenant_id: str = "default", trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """把【人审批】的画像增强提议写回长期记忆 (content_type=dim, tags=['user_analysis'])。
        非审批 / 非法 dim / 无 long_term_memory 一律跳过。human-in-loop; 闭环反哺 always-on 画像。"""
        approved = set(approved_ids or [])
        if self.long_term_memory is None:
            return {"applied": 0, "details": [{"result": "skip_no_memory"}]}
        applied = 0
        details: List[Dict[str, Any]] = []
        for p in (proposals or []):
            if not isinstance(p, dict) or p.get("id") not in approved:
                continue
            dim = str(p.get("dim") or "").strip().lower()
            content = str(p.get("content") or "").strip()
            if dim not in PROFILE_DIMS or not content:
                details.append({"id": p.get("id"), "result": f"skip_invalid:dim={dim}"})
                continue
            try:
                from long_term_memory_module import Fact
                f = Fact.make(
                    content=content, tenant_id=tenant_id, mutability="refinable",
                    content_type=dim, digest=content[:100], tags=["user_analysis"],
                )
                self.long_term_memory.add_fact(f)
                applied += 1
                details.append({"id": p.get("id"), "dim": dim, "result": "recorded", "fact_id": f.fact_id})
            except Exception as e:
                details.append({"id": p.get("id"), "result": f"error: {e}"})
        self.logger.info(f"[user_analysis] apply: {applied} 条反哺画像: trace_id={trace_id}")
        return {"applied": applied, "details": details}

    def _maybe_auto_user_analysis(self, tenant_id: str, trace_id: Optional[str] = None) -> None:
        """每 N 个任务自动跑一次 user_analysis (后台线程, advisory)。默认关 (every_n=0)。
        结果写审计通知 (人经面板/审计审阅), **不自动反哺画像** (反哺仍须 apply_user_insights 审批)。"""
        n = getattr(self, "user_analysis_every_n", 0) or 0
        if not (getattr(self, "enable_user_analysis", False) and n > 0):
            return
        self._user_analysis_counter = getattr(self, "_user_analysis_counter", 0) + 1
        if self._user_analysis_counter < n:
            return
        self._user_analysis_counter = 0

        import threading

        def _bg():
            try:
                result = self.analyze_user(tenant_id=tenant_id, trace_id=trace_id)
                self._notify_user_analysis(result, tenant_id, trace_id)
            except Exception:
                pass
        threading.Thread(target=_bg, name="user-analysis", daemon=True).start()

    def _notify_user_analysis(self, result, tenant_id, trace_id) -> None:
        """通知: 写一条 user_analysis 审计记录 (洞察/提议数)。失败静默。"""
        try:
            from datetime import datetime, timezone
            al = getattr(getattr(self, "deps", None), "audit_logger", None)
            if al is None:
                from audit_module import get_audit_logger
                al = get_audit_logger()
            al.write({
                "timestamp_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": "user_analysis", "tenant_id": tenant_id, "trace_id": trace_id,
                "insight_count": len((result or {}).get("insights", [])),
                "proposal_count": len((result or {}).get("proposals", [])),
            })
        except Exception:
            pass
