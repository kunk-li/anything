# -*- coding: utf-8 -*-
"""
bench_api 工具函数单元测试

不真实跑 benchmark(那要起 ApiService), 只测纯函数:
    - compute_percentiles
    - check_baseline 阈值判定
"""

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from benchmarks.bench_api import compute_percentiles, check_baseline


class TestComputePercentiles(unittest.TestCase):

    def test_empty_returns_zeros(self):
        p = compute_percentiles([])
        for key in ("min", "p50", "p95", "p99", "max", "mean"):
            self.assertEqual(p[key], 0.0)

    def test_single_value(self):
        p = compute_percentiles([0.5])
        self.assertEqual(p["min"], 0.5)
        self.assertEqual(p["max"], 0.5)
        self.assertEqual(p["p50"], 0.5)
        self.assertEqual(p["p99"], 0.5)
        self.assertEqual(p["mean"], 0.5)

    def test_monotonic_increasing(self):
        # 100 个等距值, p50/p95/p99 都应单调递增
        lats = [i * 0.01 for i in range(1, 101)]  # 0.01 ~ 1.00
        p = compute_percentiles(lats)
        self.assertEqual(p["min"], 0.01)
        self.assertEqual(p["max"], 1.00)
        self.assertLessEqual(p["p50"], p["p95"])
        self.assertLessEqual(p["p95"], p["p99"])

    def test_mean_calculation(self):
        lats = [1.0, 2.0, 3.0, 4.0, 5.0]
        p = compute_percentiles(lats)
        self.assertAlmostEqual(p["mean"], 3.0, places=5)


class TestCheckBaseline(unittest.TestCase):

    def _report(self, p99: float, error_rate: float = 0.0) -> dict:
        return {
            "latency_seconds": {"p99": p99},
            "error_rate": error_rate,
        }

    def _baseline(self, p99: float) -> dict:
        return {"latency_seconds": {"p99": p99}}

    def test_within_tolerance_no_failures(self):
        failures = check_baseline(
            self._report(p99=0.5, error_rate=0.01),
            self._baseline(p99=0.4),
            max_p99_ratio=1.5,
            max_error_rate=0.05,
        )
        self.assertEqual(failures, [])

    def test_p99_regression_detected(self):
        # 当前 1.0s, baseline 0.5s, ratio 1.5 -> 阈值 0.75s, 1.0 > 0.75 -> fail
        failures = check_baseline(
            self._report(p99=1.0),
            self._baseline(p99=0.5),
            max_p99_ratio=1.5,
            max_error_rate=0.05,
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("p99 latency regression", failures[0])

    def test_error_rate_too_high(self):
        failures = check_baseline(
            self._report(p99=0.4, error_rate=0.10),  # 10% > 5%
            self._baseline(p99=0.4),
            max_p99_ratio=1.5,
            max_error_rate=0.05,
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("error rate too high", failures[0])

    def test_both_fail_returns_both(self):
        failures = check_baseline(
            self._report(p99=2.0, error_rate=0.20),
            self._baseline(p99=0.5),
            max_p99_ratio=1.5,
            max_error_rate=0.05,
        )
        self.assertEqual(len(failures), 2)

    def test_no_baseline_p99_skips_p99_check(self):
        """baseline 没记录 p99 时不报 p99 regression"""
        failures = check_baseline(
            self._report(p99=5.0),
            {"latency_seconds": {}},  # 缺 p99
            max_p99_ratio=1.5,
            max_error_rate=0.05,
        )
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
