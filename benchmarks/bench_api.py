# -*- coding: utf-8 -*-
"""
API 服务性能基线测试

用 ThreadPoolExecutor 并发打 /invoke 端点,统计:
    - 总请求数 / 错误数 / 错误率
    - p50 / p95 / p99 延迟
    - QPS (吞吐量)

用途:
    - 本地建立 baseline,后续修改后跑一次对比
    - CI 阈值守护:p99 不能比 baseline 慢超过 50% / 错误率不超过 5%

跑法:
    # 本地建立 baseline (10 并发, 100 总请求)
    PYTHONPATH=... python benchmarks/bench_api.py \
        --concurrency 10 --total 100 --output benchmarks/baseline.json

    # 跟 baseline 对比, p99 高于 baseline * 1.5 -> exit 1
    PYTHONPATH=... python benchmarks/bench_api.py \
        --concurrency 10 --total 100 \
        --baseline benchmarks/baseline.json --ci

注意:
    - LLM 调用在无 API key 时走 DummyLLMClient (DEV_MODE), 延迟稳定
    - sentence-transformers 首次编码会触发模型加载, 首批请求会偏慢
    - 真实生产 SLO 需用真实 LLM 端到端测,这里只是"内部链路基线"
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
for layer in ("basic_support", "data_layer", "business", "interface", "application", "run"):
    p = str(_ROOT / layer)
    if p not in sys.path:
        sys.path.insert(0, p)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 默认开 dev_mode 让无 API key 时也能跑
os.environ.setdefault("ANYTHING_DEV_MODE", "1")


def compute_percentiles(latencies: List[float]) -> Dict[str, float]:
    """计算延迟分位数. latencies 单位是秒."""
    if not latencies:
        return {"min": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0, "mean": 0.0}
    sorted_lat = sorted(latencies)
    n = len(sorted_lat)

    def at_percentile(p: float) -> float:
        # nearest-rank method
        k = max(0, min(n - 1, int(p / 100.0 * n)))
        return sorted_lat[k]

    return {
        "min": sorted_lat[0],
        "p50": at_percentile(50),
        "p95": at_percentile(95),
        "p99": at_percentile(99),
        "max": sorted_lat[-1],
        "mean": statistics.mean(sorted_lat),
    }


def build_test_client():
    """切到 run/ 让 vector_store 相对路径解析正确, 然后构造 ApiService TestClient."""
    os.chdir(_ROOT / "run")
    from fastapi.testclient import TestClient
    from bootstrap import build_handler
    from api_service_module import ApiService

    handler = build_handler()
    service = ApiService(handler=handler)
    service.auth_enabled = False  # 关掉鉴权方便压测
    return TestClient(service.app)


def run_single(client, request_body: Dict[str, Any]) -> Tuple[float, int, str]:
    """打一次请求, 返回 (latency_s, http_status, business_code)."""
    start = time.perf_counter()
    try:
        resp = client.post("/invoke", json=request_body, timeout=30)
        latency = time.perf_counter() - start
        try:
            code = resp.json().get("code", "UNKNOWN")
        except Exception:
            code = "PARSE_ERR"
        return latency, resp.status_code, str(code)
    except Exception as e:
        return time.perf_counter() - start, 0, f"EXC:{type(e).__name__}"


def run_benchmark(
    concurrency: int,
    total: int,
    request_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """主入口: 跑 total 个请求, 用 concurrency 个线程并发."""
    client = build_test_client()
    body = request_body or {"type": "rag", "query": "什么是 RAG", "top_k": 3}

    print(f"[bench] concurrency={concurrency} total={total} body={body}")

    # 预热: 第一次请求触发 sentence-transformers 模型加载, 不计入统计
    print("[bench] warmup 1 request...")
    _ = run_single(client, body)

    latencies: List[float] = []
    success = 0
    errors_by_code: Dict[str, int] = {}

    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(run_single, client, body) for _ in range(total)]
        for fut in as_completed(futures):
            latency, http, code = fut.result()
            latencies.append(latency)
            if http == 200 and code == "SUCCESS":
                success += 1
            else:
                errors_by_code[code] = errors_by_code.get(code, 0) + 1
    wall_elapsed = time.perf_counter() - wall_start

    percentiles = compute_percentiles(latencies)
    qps = total / wall_elapsed if wall_elapsed > 0 else 0.0

    return {
        "concurrency": concurrency,
        "total": total,
        "success": success,
        "errors": total - success,
        "error_rate": (total - success) / total if total else 0.0,
        "errors_by_code": errors_by_code,
        "wall_time_seconds": round(wall_elapsed, 3),
        "qps": round(qps, 2),
        "latency_seconds": {k: round(v, 4) for k, v in percentiles.items()},
    }


def print_report(report: Dict[str, Any]) -> None:
    print("=" * 60)
    print("BENCHMARK REPORT")
    print("=" * 60)
    print(f"  concurrency: {report['concurrency']}")
    print(f"  total:       {report['total']}")
    print(f"  success:     {report['success']}")
    print(f"  errors:      {report['errors']} ({report['error_rate']:.2%})")
    if report["errors_by_code"]:
        print(f"  errors_by_code: {report['errors_by_code']}")
    print(f"  wall_time:   {report['wall_time_seconds']}s")
    print(f"  qps:         {report['qps']}")
    print(f"  latency (s):")
    lat = report["latency_seconds"]
    print(f"    min  = {lat['min']:.3f}")
    print(f"    p50  = {lat['p50']:.3f}")
    print(f"    mean = {lat['mean']:.3f}")
    print(f"    p95  = {lat['p95']:.3f}")
    print(f"    p99  = {lat['p99']:.3f}")
    print(f"    max  = {lat['max']:.3f}")


def check_baseline(report: Dict[str, Any], baseline: Dict[str, Any],
                   max_p99_ratio: float, max_error_rate: float) -> List[str]:
    failures = []

    base_p99 = baseline.get("latency_seconds", {}).get("p99", 0)
    cur_p99 = report["latency_seconds"]["p99"]
    if base_p99 > 0 and cur_p99 > base_p99 * max_p99_ratio:
        failures.append(
            f"p99 latency regression: {cur_p99:.3f}s > baseline {base_p99:.3f}s "
            f"* {max_p99_ratio} = {base_p99 * max_p99_ratio:.3f}s"
        )

    if report["error_rate"] > max_error_rate:
        failures.append(
            f"error rate too high: {report['error_rate']:.2%} > {max_error_rate:.2%}"
        )

    return failures


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--total", type=int, default=20)
    parser.add_argument("--output", type=str, default=None,
                        help="保存 report 到 JSON 文件, 用于后续 baseline 对比")
    parser.add_argument("--baseline", type=str, default=None,
                        help="跟此 JSON baseline 对比, 触发回归则报警")
    parser.add_argument("--ci", action="store_true",
                        help="CI 模式: 阈值不达标时 exit 1")
    parser.add_argument("--max-p99-ratio", type=float, default=1.5,
                        help="--ci 时, 允许 p99 最多比 baseline 慢的倍数")
    parser.add_argument("--max-error-rate", type=float, default=0.05,
                        help="--ci 时, 允许的最大错误率")
    args = parser.parse_args(argv)

    report = run_benchmark(args.concurrency, args.total)
    print_report(report)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n[bench] saved to {args.output}")

    if args.baseline and args.ci:
        try:
            baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[bench] cannot load baseline: {e}")
            return 1
        failures = check_baseline(
            report, baseline,
            max_p99_ratio=args.max_p99_ratio,
            max_error_rate=args.max_error_rate,
        )
        if failures:
            print("\nBENCHMARK FAILED:")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("\nBENCHMARK: within baseline tolerance ✓")

    return 0


if __name__ == "__main__":
    sys.exit(main())
