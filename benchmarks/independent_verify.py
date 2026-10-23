#!/usr/bin/env python3
"""Independent, self-contained verification script.

Reads raw results.jsonl directly from:
- benchmarks/results_tier1
- benchmarks/results_tier2
- benchmarks/results_tier3
- benchmarks/results_posthoc

Imports NOTHING from analyze_experiments.py or generate_missing_tables.py.
Computes N, median, mean, p95, min, max per (scenario, load, condition)
from first principles using Python standard library.
"""

import json
import statistics
from pathlib import Path
from typing import Dict, List, Tuple

BENCHMARKS = Path("/mnt/c/Os Project/benchmarks")
RAW_DIRS = ["results_tier1", "results_tier2", "results_tier3", "results_posthoc"]


def p95(data: List[float]) -> float:
    s = sorted(data)
    idx = int(math_ceil(0.95 * len(s))) - 1
    return s[max(0, min(idx, len(s) - 1))]


def math_ceil(x: float) -> int:
    i = int(x)
    return i + 1 if x > i else i


def compute_raw_cell_metrics() -> Dict[Tuple[str, str, str, str], Dict[str, float]]:
    cells: Dict[Tuple[str, str, str, str], List[float]] = {}
    for d in RAW_DIRS:
        p = BENCHMARKS / d / "results.jsonl"
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("is_warmup", False):
                continue
            t = str(r.get("tier"))
            sc = r.get("scenario")
            ld = r.get("load_level")
            cn = r.get("condition")
            w = r.get("build_wall_s", r.get("duration_wall_s"))
            if w is not None:
                cells.setdefault((t, sc, ld, cn), []).append(float(w))

    results: Dict[Tuple[str, str, str, str], Dict[str, float]] = {}
    for k, vals in sorted(cells.items()):
        s = sorted(vals)
        n = len(s)
        med = statistics.median(s)
        mean_val = statistics.mean(s)
        p95_val = p95(s)
        min_val = min(s)
        max_val = max(s)
        results[k] = {
            "n": float(n),
            "median": med,
            "mean": mean_val,
            "p95": p95_val,
            "min": min_val,
            "max": max_val,
        }
    return results


def print_table() -> None:
    metrics = compute_raw_cell_metrics()
    print("=" * 105)
    print("RAW DATA VERIFICATION: COMPUTED DIRECTLY FROM RAW results.jsonl (NO ANALYZER IMPORTS)")
    print("=" * 105)
    hdr = f"| {'Tier':<8} | {'Scen':<5} | {'Load':<8} | {'Condition':<14} | {'N':<4} | {'Median':<8} | {'Mean':<8} | {'P95':<8} | {'Min':<8} | {'Max':<8} |"
    print(hdr)
    print("|" + "|".join(["-" * 10, "-" * 7, "-" * 10, "-" * 16, "-" * 6, "-" * 10, "-" * 10, "-" * 10, "-" * 10, "-" * 10]) + "|")
    for k, m in sorted(metrics.items()):
        t, sc, ld, cn = k
        print(
            f"| {t:<8} | {sc:<5} | {ld:<8} | {cn:<14} | {int(m['n']):<4} | {m['median']:<8.4f} | {m['mean']:<8.4f} | {m['p95']:<8.4f} | {m['min']:<8.4f} | {m['max']:<8.4f} |"
        )
    print("=" * 105)


if __name__ == "__main__":
    print_table()
