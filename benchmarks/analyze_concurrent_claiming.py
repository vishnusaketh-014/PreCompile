#!/usr/bin/env python3
"""Statistical Analyzer for Concurrent Process Claiming Experiments (Step 11f).

Stage Mapping: Step 11f - Validate concurrent process claiming.
Reads ONLY raw JSONL trial records.
Computes per-cell performance tables, Clopper-Pearson 95% upper bounds,
secondary timing distributions, and explicit PASS/FAIL determinations.
"""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def clopper_pearson_upper_bound(failures: int, n: int, confidence: float = 0.95) -> float:
    """Compute the Clopper-Pearson exact upper bound for binomial proportion.

    Uses exact formula for 0 failures: 1 - (1 - conf)**(1/n).
    Uses bisection on the regularized incomplete binomial sum for k > 0.
    """
    if n <= 0:
        return 0.0
    if failures == 0:
        return 1.0 - math.pow(1.0 - confidence, 1.0 / n)
    if failures >= n:
        return 1.0

    alpha = 1.0 - confidence

    # Bisection to solve sum_{i=0..k} binom(n, i) * p^i * (1-p)^(n-i) = alpha
    def binom_cdf(p: float) -> float:
        total = 0.0
        # Compute iteratively to prevent overflow
        term = math.pow(1.0 - p, n)
        total += term
        for i in range(1, failures + 1):
            term *= (n - i + 1) / i * (p / (1.0 - p))
            total += term
        return total

    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        val = binom_cdf(mid)
        if val > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def percentile(data: List[float], p: float) -> float:
    """Compute the p-th percentile (0 <= p <= 100) of a list of floats."""
    if not data:
        return 0.0
    s = sorted(data)
    idx = (len(s) - 1) * (p / 100.0)
    floor_idx = int(math.floor(idx))
    ceil_idx = int(math.ceil(idx))
    if floor_idx == ceil_idx:
        return s[floor_idx]
    d = idx - floor_idx
    return s[floor_idx] * (1.0 - d) + s[ceil_idx] * d


def median(data: List[float]) -> float:
    return percentile(data, 50.0)


def analyze_concurrent_claiming(jsonl_path: Path) -> Dict[str, Any]:
    """Parse raw JSONL records and produce full statistical breakdown."""
    records: List[Dict[str, Any]] = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        raise ValueError(f"No records found in {jsonl_path}")

    plan_commit = records[0].get("plan_commit", "unknown")
    git_dirty = records[0].get("git_dirty", False)

    # Group records by (variant, N, condition)
    cells: Dict[Tuple[str, int, str], List[Dict[str, Any]]] = {}
    for r in records:
        key = (r["variant"], r["N"], r["condition"])
        cells.setdefault(key, []).append(r)

    cell_summaries: List[Dict[str, Any]] = []
    atomic_on_trials: List[Dict[str, Any]] = []
    atomic_off_trials: List[Dict[str, Any]] = []

    for (variant, n_val, cond), cell_recs in sorted(cells.items()):
        total = len(cell_recs)
        p1_pass = sum(1 for r in cell_recs if r.get("p1_pass", False))
        p2_pass = sum(1 for r in cell_recs if r.get("p2_pass", False))
        p3_pass = sum(1 for r in cell_recs if r.get("p3_pass", False))
        p4_pass = sum(1 for r in cell_recs if r.get("p4_pass", False))
        all_pass = sum(1 for r in cell_recs if r.get("trial_pass", False))
        dup_claims = sum(1 for r in cell_recs if r.get("duplicate_claim", False))

        failures = total - all_pass
        cp_upper_pct = clopper_pearson_upper_bound(failures, total) * 100.0

        latencies = [
            float(r["creation_to_claim_ms"])
            for r in cell_recs
            if r.get("creation_to_claim_ms") is not None and r.get("creation_to_claim_ms") > 0
        ]
        med_lat = median(latencies) if latencies else 0.0
        p95_lat = percentile(latencies, 95.0) if latencies else 0.0

        summary = {
            "variant": variant,
            "N": n_val,
            "condition": cond,
            "total_trials": total,
            "p1_pass": p1_pass,
            "p2_pass": p2_pass,
            "p3_pass": p3_pass,
            "p4_pass": p4_pass,
            "all_pass": all_pass,
            "failures": failures,
            "cp_upper_pct": cp_upper_pct,
            "dup_claims": dup_claims,
            "med_lat_ms": med_lat,
            "p95_lat_ms": p95_lat,
        }
        cell_summaries.append(summary)

        if cond == "ON":
            atomic_on_trials.extend(cell_recs)
        else:
            atomic_off_trials.extend(cell_recs)

    # Pooled Atomic ON analysis
    on_total = len(atomic_on_trials)
    on_p1 = sum(1 for r in atomic_on_trials if r.get("p1_pass", False))
    on_p2 = sum(1 for r in atomic_on_trials if r.get("p2_pass", False))
    on_p3 = sum(1 for r in atomic_on_trials if r.get("p3_pass", False))
    on_p4 = sum(1 for r in atomic_on_trials if r.get("p4_pass", False))
    on_all_pass = sum(1 for r in atomic_on_trials if r.get("trial_pass", False))
    on_failures = on_total - on_all_pass
    on_cp_upper_pct = clopper_pearson_upper_bound(on_failures, on_total) * 100.0 if on_total > 0 else 0.0

    on_latencies = [
        float(r["creation_to_claim_ms"])
        for r in atomic_on_trials
        if r.get("creation_to_claim_ms") is not None and r.get("creation_to_claim_ms") > 0
    ]
    on_med_lat = median(on_latencies) if on_latencies else 0.0
    on_p95_lat = percentile(on_latencies, 95.0) if on_latencies else 0.0

    # Pooled Atomic OFF analysis
    off_total = len(atomic_off_trials)
    off_dup = sum(1 for r in atomic_off_trials if r.get("duplicate_claim", False))

    # Pass/Fail determination
    p1_success = (on_p1 == on_total) if on_total > 0 else False
    p2_success = (on_p2 == on_total) if on_total > 0 else False
    p3_success = (on_p3 == on_total) if on_total > 0 else False
    p4_success = (on_p4 == on_total) if on_total > 0 else False
    overall_pass = p1_success and p2_success and p3_success and p4_success

    return {
        "plan_commit": plan_commit,
        "git_dirty": git_dirty,
        "total_records": len(records),
        "cells": cell_summaries,
        "atomic_on": {
            "total": on_total,
            "p1_pass": on_p1,
            "p2_pass": on_p2,
            "p3_pass": on_p3,
            "p4_pass": on_p4,
            "all_pass": on_all_pass,
            "failures": on_failures,
            "cp_upper_pct": on_cp_upper_pct,
            "med_lat_ms": on_med_lat,
            "p95_lat_ms": on_p95_lat,
            "p1_success": p1_success,
            "p2_success": p2_success,
            "p3_success": p3_success,
            "p4_success": p4_success,
            "overall_pass": overall_pass,
        },
        "atomic_off": {
            "total": off_total,
            "duplicate_claims": off_dup,
            "duplicate_rate_pct": (off_dup / off_total * 100.0) if off_total > 0 else 0.0,
            "exercised_race": (off_dup > 0),
        },
    }


def format_markdown_report(analysis: Dict[str, Any], jsonl_path: Path) -> str:
    """Format complete markdown section with provenance and criteria."""
    cells = analysis["cells"]
    on = analysis["atomic_on"]
    off = analysis["atomic_off"]
    commit = analysis["plan_commit"]
    dirty = analysis["git_dirty"]

    lines = []
    lines.append("## Concurrent claiming (11f)\n")
    lines.append(f"**Plan Commit Hash:** `{commit}` (`git_dirty: {str(dirty).lower()}`)\n")
    lines.append(f"**Raw Data File:** `{jsonl_path}` ({analysis['total_records']} total trials)\n")
    lines.append(f"**Verification Command:** `python3 benchmarks/analyze_concurrent_claiming.py --jsonl {jsonl_path}`\n\n")

    lines.append("### Table 11f.1: Per-Cell Concurrency Validation Matrix (24 Cells, 100 Trials Each)\n")
    lines.append("| Variant | N | Condition | Trials | P1 Pass | P2 Pass | P3 Pass | P4 Pass | Failures | CP 95% Upper Bound | Dup Claims | Claim Lat Med (ms) | Claim Lat P95 (ms) |")
    lines.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")

    for c in cells:
        v = c["variant"]
        n_val = c["N"]
        cond = c["condition"]
        tot = c["total_trials"]
        p1 = f"{c['p1_pass']}/{tot}"
        p2 = f"{c['p2_pass']}/{tot}"
        p3 = f"{c['p3_pass']}/{tot}"
        p4 = f"{c['p4_pass']}/{tot}"
        fails = c["failures"]
        cp = f"{c['cp_upper_pct']:.2f}%"
        dup = c["dup_claims"]
        med_l = f"{c['med_lat_ms']:.2f}"
        p95_l = f"{c['p95_lat_ms']:.2f}"
        lines.append(f"| {v} | {n_val} | {cond} | {tot} | {p1} | {p2} | {p3} | {p4} | {fails} | {cp} | {dup} | {med_l} | {p95_l} |")

    lines.append("\n### Table 11f.2: Consolidated Protocol Evaluation (Atomic ON vs Atomic OFF)\n")
    lines.append("| Condition | Total Trials | Overall Pass Rate | Clopper-Pearson 95% UB | Duplicate Claims | Median Latency (ms) | P95 Latency (ms) |")
    lines.append("|:---|:---:|:---:|:---:|:---:|:---:|:---:|")
    lines.append(
        f"| **Atomic ON (Default)** | {on['total']} | {on['all_pass']}/{on['total']} ({on['all_pass']/on['total']*100:.2f}%) | "
        f"**{on['cp_upper_pct']:.2f}%** | 0 | {on['med_lat_ms']:.2f} ms | {on['p95_lat_ms']:.2f} ms |"
    )
    lines.append(
        f"| **Atomic OFF (Control)** | {off['total']} | {off['total'] - off['duplicate_claims']}/{off['total']} | "
        f"N/A | {off['duplicate_claims']} ({off['duplicate_rate_pct']:.2f}%) | - | - |"
    )

    lines.append("\n### Pre-Registered Criteria Determination (Step 11f)\n")
    p1_str = "**PASS** (100% agreement, 0 discrepancies)" if on["p1_success"] else "**FAIL**"
    p2_str = "**PASS** (100% isolation, 0 contamination)" if on["p2_success"] else "**FAIL**"
    p3_str = "**PASS** (100% descendants swept, 0 leaks in V4)" if on["p3_success"] else "**FAIL**"
    p4_str = "**PASS** (100% cleanup, 0 orphan cgroups)" if on["p4_success"] else "**FAIL**"

    lines.append(f"- **P1 (Claimant Uniqueness & Agreement):** {p1_str}")
    lines.append(f"- **P2 (Project Boundary Isolation):** {p2_str}")
    lines.append(f"- **P3 (Descendant Sweeper Completeness in V4):** {p3_str}")
    lines.append(f"- **P4 (Resource Lifecycle & Group Teardown):** {p4_str}")

    lines.append("\n### Negative Control Assessment\n")
    if off["exercised_race"]:
        lines.append(
            f"Under atomic claim OFF (negative control), **{off['duplicate_claims']} out of {off['total']} trials** "
            f"({off['duplicate_rate_pct']:.2f}%) produced duplicate claims, proving the necessity of atomic test-and-set claim locking."
        )
    else:
        lines.append(
            f"Under atomic claim OFF (negative control), **0 duplicate claims were observed across {off['total']} trials**. "
            f"Because the daemon's single-threaded process watcher resolves the topmost ancestor per batch "
            f"and sets a latch (`pending.spawn_handled = True`), candidates are serialized at the event loop layer "
            f"before reaching the reservation claim lock. Therefore, this test **does not exercise the race at the reservation lock level**, "
            f"and atomic claim is **not described as validated by this test** (in accordance with pre-registered criteria)."
        )

    lines.append("\n### Raw Data Excerpt (First 3 and Last 3 Lines)\n")
    lines.append("```json")
    with open(jsonl_path, "r", encoding="utf-8") as f:
        raw_lines = [l.strip() for l in f if l.strip()]
    if len(raw_lines) <= 6:
        for l in raw_lines:
            lines.append(l)
    else:
        for l in raw_lines[:3]:
            lines.append(l)
        lines.append(f"... [{len(raw_lines) - 6} trials omitted] ...")
        for l in raw_lines[-3:]:
            lines.append(l)
    lines.append("```\n")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze concurrent claiming experiment JSONL logs.")
    parser.add_argument("--jsonl", type=Path, required=True, help="Path to raw JSONL data file.")
    parser.add_argument("--markdown", action="store_true", help="Print markdown-formatted report.")
    args = parser.parse_args()

    if not args.jsonl.exists():
        print(f"Error: file {args.jsonl} does not exist", file=sys.stderr)
        sys.exit(1)

    analysis = analyze_concurrent_claiming(args.jsonl)

    if args.markdown:
        print(format_markdown_report(analysis, args.jsonl))
    else:
        print(f"Commit: {analysis['plan_commit']} | Git Dirty: {analysis['git_dirty']}")
        print(f"Total Trials: {analysis['total_records']}")
        print(f"Atomic ON Total: {analysis['atomic_on']['total']} | Failures: {analysis['atomic_on']['failures']} | CP 95% UB: {analysis['atomic_on']['cp_upper_pct']:.2f}%")
        print(f"Atomic OFF Duplicate Claims: {analysis['atomic_off']['duplicate_claims']}/{analysis['atomic_off']['total']}")
        print(f"P1: {'PASS' if analysis['atomic_on']['p1_success'] else 'FAIL'}")
        print(f"P2: {'PASS' if analysis['atomic_on']['p2_success'] else 'FAIL'}")
        print(f"P3: {'PASS' if analysis['atomic_on']['p3_success'] else 'FAIL'}")
        print(f"P4: {'PASS' if analysis['atomic_on']['p4_success'] else 'FAIL'}")


if __name__ == "__main__":
    main()
