# -*- coding: utf-8 -*-
"""
RAG / Agent 业务质量评测脚本

设计目标:
    - 单测覆盖契约级正确性(签名/字段);本脚本覆盖业务质量(召回率/工具选择/答案完整性)
    - 数据集格式 JSONL,每行一个 case,便于增删 + diff 友好
    - 提供 --ci 模式 + 阈值参数,便于本地或专用 CI 任务跑红线守护

使用:
    # 本地跑评测(读取所有数据集,输出指标)
    python evaluation/run_eval.py

    # 只跑 RAG 评测
    python evaluation/run_eval.py --dataset evaluation/datasets/rag_basic.jsonl

    # CI 阈值守护(任一指标低于阈值则 exit 1)
    python evaluation/run_eval.py --ci --rag-recall-threshold 0.6 --agent-success-threshold 0.75

依赖:
    需要 PYTHONPATH 包含 basic_support/data_layer/business/interface/application/run
    需要数据层有已索引的文档 (run/index_build.py 跑过) 或在 dev_mode 下空检索也能跑

指标定义:
    RAG:
      - recall_keyword: 答案/检索 chunks 中是否包含 expected_content_keywords (软指标)
      - file_hit:      检索 chunks 的 file_name 是否含 expected_file_keywords
      - has_citations: 是否产出了 citations (基础健康性)
    Agent:
      - code_match:     响应 code 是否 == expected_code
      - tools_match:    步骤的工具是否包含 expected_tools 中至少一个
      - answer_nonempty: 答案长度是否 >= expected_answer_min_length
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# 让脚本可被直接 python 执行(把项目源路径加进 sys.path)
_ROOT = Path(__file__).resolve().parent.parent
for layer in ("basic_support", "data_layer", "business", "interface", "application", "run"):
    p = str(_ROOT / layer)
    if p not in sys.path:
        sys.path.insert(0, p)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@dataclass
class CaseResult:
    case_id: str
    case_type: str
    passed: bool
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    cost_time: float = 0.0


@dataclass
class EvalReport:
    rag_total: int = 0
    rag_passed: int = 0
    rag_recall_keyword: float = 0.0  # 平均
    rag_file_hit: float = 0.0
    rag_has_citations: float = 0.0
    # Task R: 进阶 IR 指标 (仅在 case 提供 expected_doc_ids 时计算, 否则保持 None)
    rag_recall_at_k: Optional[float] = None
    rag_mrr: Optional[float] = None
    agent_total: int = 0
    agent_passed: int = 0
    agent_code_match: float = 0.0
    agent_tools_match: float = 0.0
    agent_answer_nonempty: float = 0.0
    case_results: List[CaseResult] = field(default_factory=list)
    mode_label: str = ""  # Task R: "vector-only" / "hybrid" / 空字符串

    def print_summary(self) -> None:
        print("=" * 60)
        header = "EVALUATION SUMMARY"
        if self.mode_label:
            header += f" — mode={self.mode_label}"
        print(header)
        print("=" * 60)
        if self.rag_total:
            print(f"\nRAG cases: {self.rag_total}, passed: {self.rag_passed} "
                  f"({self.rag_passed / self.rag_total:.1%})")
            print(f"  recall_keyword: {self.rag_recall_keyword:.2%}")
            print(f"  file_hit:       {self.rag_file_hit:.2%}")
            print(f"  has_citations:  {self.rag_has_citations:.2%}")
            if self.rag_recall_at_k is not None:
                print(f"  recall@k:       {self.rag_recall_at_k:.2%}  (over cases with expected_doc_ids)")
            if self.rag_mrr is not None:
                print(f"  MRR:            {self.rag_mrr:.3f}  (over cases with expected_doc_ids)")
        if self.agent_total:
            print(f"\nAgent cases: {self.agent_total}, passed: {self.agent_passed} "
                  f"({self.agent_passed / self.agent_total:.1%})")
            print(f"  code_match:      {self.agent_code_match:.2%}")
            print(f"  tools_match:     {self.agent_tools_match:.2%}")
            print(f"  answer_nonempty: {self.agent_answer_nonempty:.2%}")
        print()


def load_dataset(path: Path) -> List[Dict[str, Any]]:
    cases = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cases.append(json.loads(line))
    return cases


def evaluate_rag_case(handler, case: Dict[str, Any]) -> CaseResult:
    start = time.time()
    cid = case["id"]
    query = case.get("query", "")
    top_k = int(case.get("top_k", 5))
    expected_file_kw = case.get("expected_file_keywords", []) or []
    expected_content_kw = case.get("expected_content_keywords", []) or []
    # Task #33 PR4b: 多租户字段
    tenant_id = case.get("tenant_id")
    expected_must_not_contain = case.get("expected_must_not_contain", []) or []
    expected_code = case.get("expected_code")  # 默认 SUCCESS; mt 集中可能是 TENANT_NOT_FOUND
    allow_zero_chunks = bool(case.get("allow_zero_chunks", False))

    # 构造 request body, 显式带 tenant_id (PR1 透传)
    body = {"type": "rag", "query": query, "top_k": top_k}
    if tenant_id is not None:
        body["tenant_id"] = tenant_id

    try:
        resp = handler.handle(body, trace_id=f"eval_{cid}")
    except Exception as e:
        return CaseResult(case_id=cid, case_type="rag", passed=False,
                          error=str(e), cost_time=time.time() - start)

    data = resp.get("data") or {}
    answer = str(data.get("answer", "") or "")
    citations = data.get("citations") or []
    chunks = data.get("retrieved_chunks") or []

    # Task R: 进阶 IR 指标 — recall@k + MRR (仅当 case 提供 expected_doc_ids)
    expected_doc_ids = case.get("expected_doc_ids") or []
    retrieved_doc_ids = [str(c.get("doc_id", "") or "") for c in chunks]
    recall_at_k: Optional[float] = None
    mrr: Optional[float] = None
    if expected_doc_ids:
        relevant_set = set(expected_doc_ids)
        hits = sum(1 for did in retrieved_doc_ids if did in relevant_set)
        recall_at_k = hits / len(expected_doc_ids)
        # MRR: 第一个命中相关 doc 的 1/rank
        mrr = 0.0
        for rank, did in enumerate(retrieved_doc_ids, start=1):
            if did in relevant_set:
                mrr = 1.0 / rank
                break

    # file_hit: 任一 expected file keyword 出现在检索到的 file_name 中
    file_names = [str(c.get("file_name", "") or "") for c in chunks]
    file_hit = 1.0 if (not expected_file_kw or any(
        any(kw in fn for fn in file_names) for kw in expected_file_kw
    )) else 0.0

    # recall_keyword: 答案 + chunk_id 列表中是否包含 expected_content_keywords
    haystack = answer + " " + " ".join(file_names)
    if expected_content_kw:
        hit_kw = sum(1 for kw in expected_content_kw if kw in haystack)
        recall_kw = hit_kw / len(expected_content_kw)
    else:
        recall_kw = 1.0

    # PR4b 多租户硬断言: 检索到的 chunks 不应含其他 tenant 关键词
    leak_violation = False
    if expected_must_not_contain:
        leak_haystack = haystack + " " + " ".join(
            str(c.get("chunk_id", "") or "") + " " + str(c.get("text", "") or "")
            for c in chunks
        )
        for forbid_kw in expected_must_not_contain:
            if forbid_kw in leak_haystack:
                leak_violation = True
                break

    has_citations = 1.0 if citations else 0.0

    # 通过判定:
    # - expected_code != SUCCESS (e.g. TENANT_NOT_FOUND) -> 严格比较 code
    # - 否则 code == SUCCESS 且 (允许 0 chunks 时不查 file_hit) 且 recall_kw 达标
    code = resp.get("code")
    if expected_code and expected_code != "SUCCESS":
        passed = code == expected_code and not leak_violation
    else:
        passed = (
            code == "SUCCESS"
            and (allow_zero_chunks or file_hit == 1.0)
            and recall_kw >= 0.5
            and not leak_violation
        )

    metrics: Dict[str, Any] = {
        "recall_keyword": recall_kw,
        "file_hit": file_hit,
        "has_citations": has_citations,
        "code": code,
        "chunks_count": len(chunks),
        "leak_violation": leak_violation,
        "tenant_id": tenant_id,
    }
    if recall_at_k is not None:
        metrics["recall_at_k"] = recall_at_k
    if mrr is not None:
        metrics["mrr"] = mrr
    return CaseResult(
        case_id=cid, case_type="rag", passed=passed,
        metrics=metrics, cost_time=time.time() - start,
    )


def evaluate_agent_case(handler, case: Dict[str, Any]) -> CaseResult:
    start = time.time()
    cid = case["id"]
    task = case.get("task", "")
    rtype = case.get("type", "agent")
    expected_tools = set(case.get("expected_tools", []) or [])
    min_len = int(case.get("expected_answer_min_length", 1))
    expected_code = case.get("expected_code", "SUCCESS")

    try:
        resp = handler.handle(
            {"type": rtype, "task": task},
            trace_id=f"eval_{cid}",
        )
    except Exception as e:
        return CaseResult(case_id=cid, case_type=rtype, passed=False,
                          error=str(e), cost_time=time.time() - start)

    data = resp.get("data") or {}
    answer = str(data.get("answer", "") or "")
    steps = data.get("steps") or []
    used_tools = {s.get("tool_name") for s in steps if isinstance(s, dict)}

    code_match = 1.0 if resp.get("code") == expected_code else 0.0
    tools_match = 1.0 if (
        not expected_tools
        or used_tools & expected_tools
    ) else 0.0
    answer_nonempty = 1.0 if len(answer) >= min_len else 0.0

    passed = code_match == 1.0 and tools_match == 1.0 and answer_nonempty == 1.0
    return CaseResult(
        case_id=cid, case_type=rtype, passed=passed,
        metrics={
            "code_match": code_match,
            "tools_match": tools_match,
            "answer_nonempty": answer_nonempty,
            "code": resp.get("code"),
            "used_tools": list(used_tools),
            "answer_length": len(answer),
        },
        cost_time=time.time() - start,
    )


def aggregate_report(results: List[CaseResult]) -> EvalReport:
    report = EvalReport(case_results=results)

    rag = [r for r in results if r.case_type == "rag"]
    agent = [r for r in results if r.case_type in ("agent", "hybrid")]

    if rag:
        report.rag_total = len(rag)
        report.rag_passed = sum(1 for r in rag if r.passed)
        report.rag_recall_keyword = (
            sum(r.metrics.get("recall_keyword", 0.0) for r in rag) / len(rag)
        )
        report.rag_file_hit = (
            sum(r.metrics.get("file_hit", 0.0) for r in rag) / len(rag)
        )
        report.rag_has_citations = (
            sum(r.metrics.get("has_citations", 0.0) for r in rag) / len(rag)
        )
        # Task R: recall@k / MRR 只在提供 expected_doc_ids 的 case 上算 (avoid 拉平)
        rag_with_gt = [r for r in rag if "recall_at_k" in r.metrics]
        if rag_with_gt:
            report.rag_recall_at_k = (
                sum(r.metrics["recall_at_k"] for r in rag_with_gt) / len(rag_with_gt)
            )
            report.rag_mrr = (
                sum(r.metrics["mrr"] for r in rag_with_gt) / len(rag_with_gt)
            )

    if agent:
        report.agent_total = len(agent)
        report.agent_passed = sum(1 for r in agent if r.passed)
        report.agent_code_match = (
            sum(r.metrics.get("code_match", 0.0) for r in agent) / len(agent)
        )
        report.agent_tools_match = (
            sum(r.metrics.get("tools_match", 0.0) for r in agent) / len(agent)
        )
        report.agent_answer_nonempty = (
            sum(r.metrics.get("answer_nonempty", 0.0) for r in agent) / len(agent)
        )

    return report


def _run_single_mode(
    dataset_paths: List[Path],
    verbose: bool,
    mode_label: str = "",
) -> EvalReport:
    """跑一遍全量数据集, 返回汇总. mode_label 在 print_summary 头里显示."""
    from bootstrap import build_handler  # 延迟 import: handler 构造会 load embedding 模型
    handler = build_handler()

    all_results: List[CaseResult] = []
    for ds_path in dataset_paths:
        cases = load_dataset(ds_path)
        print(f"\n[{ds_path.name}] {len(cases)} 个 case")
        for case in cases:
            case_type = case.get("type", "rag")
            if case_type == "rag":
                r = evaluate_rag_case(handler, case)
            else:
                r = evaluate_agent_case(handler, case)
            all_results.append(r)
            status = "PASS" if r.passed else "FAIL"
            print(f"  [{status}] {r.case_id} ({r.case_type}) - {r.cost_time:.2f}s"
                  + (f" - error: {r.error}" if r.error else ""))
            if verbose and r.metrics:
                print(f"       metrics: {r.metrics}")

    report = aggregate_report(all_results)
    report.mode_label = mode_label
    report.print_summary()
    return report


def _run_compare(
    dataset_paths: List[Path],
    verbose: bool,
    ci: bool,
    regression_tol: float,
) -> int:
    """Task R: 跑 vector-only + hybrid 两遍, 对比指标. CI 模式下若 hybrid 比 vector
    在 recall_keyword / file_hit 上下降 > regression_tol, 返回 1.
    """
    print("\n\n" + "=" * 60)
    print("PASS 1 — vector-only (ANYTHING_RAG_ENABLE_HYBRID=0)")
    print("=" * 60)
    os.environ["ANYTHING_RAG_ENABLE_HYBRID"] = "0"
    # 必须强制重建 handler, 因为 build_handler 在第一遍已经 cache 了 enable_hybrid_search
    _drop_bootstrap_modules()
    rep_vec = _run_single_mode(dataset_paths, verbose=verbose, mode_label="vector-only")

    print("\n\n" + "=" * 60)
    print("PASS 2 — hybrid (ANYTHING_RAG_ENABLE_HYBRID=1)")
    print("=" * 60)
    os.environ["ANYTHING_RAG_ENABLE_HYBRID"] = "1"
    _drop_bootstrap_modules()
    rep_hyb = _run_single_mode(dataset_paths, verbose=verbose, mode_label="hybrid")

    # ============ Diff ============
    print("\n" + "=" * 60)
    print("COMPARE — hybrid vs vector-only")
    print("=" * 60)

    def _diff(label: str, vec: Optional[float], hyb: Optional[float]) -> Optional[float]:
        if vec is None and hyb is None:
            return None
        v = vec or 0.0
        h = hyb or 0.0
        d = h - v
        arrow = "↑" if d > 0.001 else ("↓" if d < -0.001 else "=")
        print(f"  {label:20s}  vector={v:7.2%}  hybrid={h:7.2%}  Δ={d:+.2%}  {arrow}")
        return d

    print("\nRAG:")
    delta_recall = _diff("recall_keyword", rep_vec.rag_recall_keyword, rep_hyb.rag_recall_keyword)
    delta_file_hit = _diff("file_hit", rep_vec.rag_file_hit, rep_hyb.rag_file_hit)
    _diff("has_citations", rep_vec.rag_has_citations, rep_hyb.rag_has_citations)
    if rep_vec.rag_recall_at_k is not None or rep_hyb.rag_recall_at_k is not None:
        _diff("recall@k", rep_vec.rag_recall_at_k, rep_hyb.rag_recall_at_k)
    if rep_vec.rag_mrr is not None or rep_hyb.rag_mrr is not None:
        # MRR 不是百分比, 单独打印
        v = rep_vec.rag_mrr or 0.0
        h = rep_hyb.rag_mrr or 0.0
        arrow = "↑" if h - v > 0.001 else ("↓" if h - v < -0.001 else "=")
        print(f"  {'MRR':20s}  vector={v:.3f}    hybrid={h:.3f}    Δ={h - v:+.3f}  {arrow}")

    if ci:
        regressions = []
        if delta_recall is not None and delta_recall < -regression_tol:
            regressions.append(f"recall_keyword Δ={delta_recall:+.2%} 超出容忍 {-regression_tol:+.2%}")
        if delta_file_hit is not None and delta_file_hit < -regression_tol:
            regressions.append(f"file_hit Δ={delta_file_hit:+.2%} 超出容忍 {-regression_tol:+.2%}")
        if regressions:
            print("\nCI HYBRID REGRESSION DETECTED:")
            for r in regressions:
                print(f"  - {r}")
            return 1
        print("\nCI: hybrid 未明显回归 ✓ (或反而提升)")
    return 0


def _drop_bootstrap_modules() -> None:
    """让下一次 import bootstrap 重新 build_basic_deps + 重读 config (因为 enable_hybrid_search
    被 ConfigManager 缓存在 SimpleRAG 实例里, 切换模式需要新建 handler).
    """
    import importlib
    for mod_name in list(sys.modules):
        if mod_name.startswith(("bootstrap", "rag_module", "config_module", "deps_module",
                                "request_response_module", "api_service_module",
                                "agent_module", "orchestrator_module")):
            sys.modules.pop(mod_name, None)
    _ = importlib  # silence linter


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset", action="append",
        help="数据集 JSONL 路径,可多次指定;不指定则跑 evaluation/datasets/ 下全部",
    )
    parser.add_argument("--ci", action="store_true",
                        help="CI 模式: 指标低于阈值时 exit 1")
    parser.add_argument("--rag-recall-threshold", type=float, default=0.0,
                        help="CI 模式下 rag_recall_keyword 阈值(默认 0,不校验)")
    parser.add_argument("--rag-file-hit-threshold", type=float, default=0.0,
                        help="CI 模式下 rag_file_hit 阈值")
    parser.add_argument("--agent-success-threshold", type=float, default=0.0,
                        help="CI 模式下 agent 通过率阈值")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="打印每个 case 的详细 metrics")
    parser.add_argument(
        "--mode", choices=["auto", "vector", "hybrid"], default="auto",
        help="检索模式 (Task R): auto=用配置; vector=强制关 hybrid; hybrid=强制开 hybrid",
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="Task R: 跑两遍 (vector + hybrid) 并打印两组指标 diff. 用来回归 hybrid 是否提升",
    )
    parser.add_argument(
        "--hybrid-regression-tolerance", type=float, default=0.05,
        help="--compare 模式下: hybrid 相对 vector 在 recall_keyword/file_hit 上允许的下降幅度 "
             "(超过则 CI exit 1). 默认 0.05 (5 个百分点).",
    )
    args = parser.parse_args(argv)

    # 默认开 dev_mode 避免无 API key 时启动失败
    os.environ.setdefault("ANYTHING_DEV_MODE", "1")

    # Task R: 显式 mode 覆盖 hybrid 开关 (compare 模式自己处理两遍)
    if args.mode == "vector":
        os.environ["ANYTHING_RAG_ENABLE_HYBRID"] = "0"
    elif args.mode == "hybrid":
        os.environ["ANYTHING_RAG_ENABLE_HYBRID"] = "1"

    # 找数据集 (在 chdir 之前先 resolve 路径,避免后续找不到)
    datasets_dir = _ROOT / "evaluation" / "datasets"
    dataset_paths = (
        [Path(p).resolve() for p in args.dataset]
        if args.dataset
        else sorted(datasets_dir.glob("*.jsonl"))
    )
    if not dataset_paths:
        print("ERROR: 找不到任何数据集 (.jsonl)")
        return 1

    # 切到 run/ 目录,让 vector_store / state_store / documents 等
    # 相对路径配置解析到已索引的数据上 (跟 run_smoke_test.py / index_build.py 一致)
    os.chdir(_ROOT / "run")

    # Task R: --compare 模式: 跑两遍 (vector + hybrid) 然后对比
    if args.compare:
        return _run_compare(
            dataset_paths=dataset_paths,
            verbose=args.verbose,
            ci=args.ci,
            regression_tol=args.hybrid_regression_tolerance,
        )

    report = _run_single_mode(
        dataset_paths=dataset_paths,
        verbose=args.verbose,
        mode_label=args.mode if args.mode != "auto" else "",
    )

    if args.ci:
        ci_failed = []
        if report.rag_total and report.rag_recall_keyword < args.rag_recall_threshold:
            ci_failed.append(f"rag_recall_keyword {report.rag_recall_keyword:.2%}"
                             f" < threshold {args.rag_recall_threshold:.2%}")
        if report.rag_total and report.rag_file_hit < args.rag_file_hit_threshold:
            ci_failed.append(f"rag_file_hit {report.rag_file_hit:.2%}"
                             f" < threshold {args.rag_file_hit_threshold:.2%}")
        if report.agent_total:
            agent_success = report.agent_passed / report.agent_total
            if agent_success < args.agent_success_threshold:
                ci_failed.append(f"agent_pass_rate {agent_success:.2%}"
                                 f" < threshold {args.agent_success_threshold:.2%}")
        if ci_failed:
            print("CI THRESHOLD FAILED:")
            for f in ci_failed:
                print(f"  - {f}")
            return 1
        print("CI: all thresholds met ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
