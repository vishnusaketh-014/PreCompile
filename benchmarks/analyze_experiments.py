#!/usr/bin/env python3
"""PreCompile Experiment Analysis Suite.

Stage Mapping: Step 11a / Step 11b Part C.
Reads benchmark JSONL records, computes summary statistics, Mann-Whitney U tests,
Holm-Bonferroni adjusted p-values, and 95% non-parametric bootstrap confidence intervals.
Outputs Markdown tables and CSV summaries.
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from precompile.stats import (
    bootstrap_relative_effect_ci,
    mann_whitney_u,
    median,
    percentile,
)


def calc_mean_std(data: List[float]) -> Tuple[float, float]:
    if not data:
        return 0.0, 0.0
    m = sum(data) / len(data)
    if len(data) < 2:
        return m, 0.0
    var = sum((x - m) ** 2 for x in data) / (len(data) - 1)
    return m, math.sqrt(max(0.0, var))


def holm_bonferroni(p_values: List[float]) -> List[float]:
    """Compute step-down Holm-Bonferroni adjusted p-values."""
    m = len(p_values)
    if m == 0:
        return []
    # Indexed sorting
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    adjusted = [0.0] * m
    running_max = 0.0

    for rank, (orig_idx, p_val) in enumerate(indexed):
        multiplier = m - rank
        adj = min(1.0, p_val * multiplier)
        running_max = max(running_max, adj)
        adjusted[orig_idx] = min(1.0, running_max)

    return adjusted


def analyze_experiments(jsonl_path: Path, output_dir: Optional[Path] = None) -> Dict[str, Any]:
    if not jsonl_path.exists():
        print(f"Error: JSONL file {jsonl_path} does not exist.", file=sys.stderr)
        return {}

    records: List[Dict[str, Any]] = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if not rec.get("is_warmup", False):
                    records.append(rec)
            except Exception:
                pass

    if not records:
        print("No valid measured records found in JSONL.", file=sys.stderr)
        return {}

    # Group by (scenario, load_level, condition)
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for r in records:
        key = (r.get("scenario", "A"), r.get("load_level", "high"), r.get("condition", "c0-baseline"))
        groups.setdefault(key, []).append(r)

    metrics_summary: List[Dict[str, Any]] = []

    for (sc, ld, cn), recs in sorted(groups.items()):
        durations = [float(r["duration_wall_s"]) for r in recs if r.get("duration_wall_s") is not None]
        mean_dur, std_dur = calc_mean_std(durations)
        med_dur = median(durations) if durations else 0.0
        p25_dur = percentile(durations, 25.0) if durations else 0.0
        p75_dur = percentile(durations, 75.0) if durations else 0.0

        claims = [r for r in recs if r.get("claimed", False)]
        claim_latencies = [
            float(r["claim_latency_ms"])
            for r in claims
            if r.get("claim_latency_ms") is not None
        ]
        mean_lat, std_lat = calc_mean_std(claim_latencies)
        med_lat = median(claim_latencies) if claim_latencies else None

        cpu_shares = [
            float(r["cgroup_cpu_share"])
            for r in recs
            if r.get("cgroup_cpu_share") is not None
        ]
        mean_share, _ = calc_mean_std(cpu_shares)

        metrics_summary.append({
            "scenario": sc,
            "load_level": ld,
            "condition": cn,
            "count": len(recs),
            "med_wall_s": med_dur,
            "mean_wall_s": mean_dur,
            "std_wall_s": std_dur,
            "p25_wall_s": p25_dur,
            "p75_wall_s": p75_dur,
            "claims_count": len(claims),
            "claim_rate": (len(claims) / len(recs)) if recs else 0.0,
            "med_claim_lat_ms": med_lat,
            "mean_claim_lat_ms": mean_lat if claim_latencies else None,
            "mean_cgroup_share": mean_share if cpu_shares else None,
            "raw_durations": durations,
            "raw_latencies": claim_latencies,
        })

    # Perform statistical comparisons for pre-registered hypotheses
    comparisons: List[Dict[str, Any]] = []

    # Map for easy lookup: (scenario, load, cond) -> entry
    lookup = {(m["scenario"], m["load_level"], m["condition"]): m for m in metrics_summary}

    scenarios = sorted(list(set(m["scenario"] for m in metrics_summary)))
    loads = sorted(list(set(m["load_level"] for m in metrics_summary)))

    for sc in scenarios:
        for ld in loads:
            c0 = lookup.get((sc, ld, "c0-baseline"))
            c1 = lookup.get((sc, ld, "c1-reactive"))
            c2 = lookup.get((sc, ld, "c2-full"))
            c3 = lookup.get((sc, ld, "c3-static"))

            # 1. C2 vs C0 Speedup
            if c2 and c0 and len(c2["raw_durations"]) >= 2 and len(c0["raw_durations"]) >= 2:
                _, _, _, p_val = mann_whitney_u(c2["raw_durations"], c0["raw_durations"])
                rel_speedup = (c0["med_wall_s"] - c2["med_wall_s"]) / c0["med_wall_s"] if c0["med_wall_s"] > 0 else 0.0
                _, ci_low, ci_high = bootstrap_relative_effect_ci(
                    {"all": c2["raw_durations"]}, {"all": c0["raw_durations"]}, n_resamples=1000
                )
                comparisons.append({
                    "hypothesis": f"H1: C2 vs C0 Speedup ({sc}, {ld})",
                    "scenario": sc,
                    "load_level": ld,
                    "comp_type": "C2 vs C0 Duration",
                    "base_med": c0["med_wall_s"],
                    "c2_med": c2["med_wall_s"],
                    "relative_speedup": rel_speedup,
                    "ci_low": ci_low,
                    "ci_high": ci_high,
                    "raw_p": p_val,
                })

            # 2. C2 vs C1 Speedup
            if c2 and c1 and len(c2["raw_durations"]) >= 2 and len(c1["raw_durations"]) >= 2:
                _, _, _, p_val = mann_whitney_u(c2["raw_durations"], c1["raw_durations"])
                rel_speedup = (c1["med_wall_s"] - c2["med_wall_s"]) / c1["med_wall_s"] if c1["med_wall_s"] > 0 else 0.0
                _, ci_low, ci_high = bootstrap_relative_effect_ci(
                    {"all": c2["raw_durations"]}, {"all": c1["raw_durations"]}, n_resamples=1000
                )
                comparisons.append({
                    "hypothesis": f"H2: C2 vs C1 Anticipatory Advantage ({sc}, {ld})",
                    "scenario": sc,
                    "load_level": ld,
                    "comp_type": "C2 vs C1 Duration",
                    "base_med": c1["med_wall_s"],
                    "c2_med": c2["med_wall_s"],
                    "relative_speedup": rel_speedup,
                    "ci_low": ci_low,
                    "ci_high": ci_high,
                    "raw_p": p_val,
                })

            # 3. C2 vs C1 Claim Latency
            if c2 and c1 and len(c2["raw_latencies"]) >= 2 and len(c1["raw_latencies"]) >= 2:
                _, _, _, p_val = mann_whitney_u(c2["raw_latencies"], c1["raw_latencies"])
                c1_lat = c1["med_claim_lat_ms"] or 0.0
                c2_lat = c2["med_claim_lat_ms"] or 0.0
                rel_red = (c1_lat - c2_lat) / c1_lat if c1_lat > 0 else 0.0
                _, ci_low, ci_high = bootstrap_relative_effect_ci(
                    {"all": c2["raw_latencies"]}, {"all": c1["raw_latencies"]}, n_resamples=1000
                )
                comparisons.append({
                    "hypothesis": f"H3: C2 vs C1 Claim Latency ({sc}, {ld})",
                    "scenario": sc,
                    "load_level": ld,
                    "comp_type": "C2 vs C1 Latency",
                    "base_med": c1_lat,
                    "c2_med": c2_lat,
                    "relative_speedup": rel_red,
                    "ci_low": ci_low,
                    "ci_high": ci_high,
                    "raw_p": p_val,
                })

    # Apply Holm-Bonferroni correction
    raw_p_list = [c["raw_p"] for c in comparisons]
    adj_p_list = holm_bonferroni(raw_p_list)
    for i, c in enumerate(comparisons):
        c["holm_p"] = adj_p_list[i]
        c["significant"] = adj_p_list[i] < 0.05

    # Print Markdown Tables
    print("\n" + "=" * 110)
    print("PRECOMPILE EVALUATION SUMMARY: BUILD DURATIONS & SYSTEM METRICS")
    print("=" * 110)
    print(f"| {'Scenario':<8} | {'Load':<8} | {'Condition':<12} | {'N':<4} | {'Med Wall':<10} | {'Mean ± Std':<16} | {'Claims':<8} | {'Med Lat':<10} | {'CPU Share':<10} |")
    print(f"|{'-'*10}|{'-'*10}|{'-'*14}|{'-'*6}|{'-'*12}|{'-'*18}|{'-'*10}|{'-'*12}|{'-'*12}|")
    for m in metrics_summary:
        med_w = f"{m['med_wall_s']:.2f}s"
        mean_std = f"{m['mean_wall_s']:.2f}±{m['std_wall_s']:.2f}s"
        clm = f"{m['claims_count']}/{m['count']}"
        med_l = f"{m['med_claim_lat_ms']:.1f}ms" if m['med_claim_lat_ms'] is not None else "N/A"
        share = f"{m['mean_cgroup_share']*100:.1f}%" if m['mean_cgroup_share'] is not None else "N/A"
        print(f"| {m['scenario']:<8} | {m['load_level']:<8} | {m['condition']:<12} | {m['count']:<4} | {med_w:<10} | {mean_std:<16} | {clm:<8} | {med_l:<10} | {share:<10} |")
    print("=" * 110)

    if comparisons:
        print("\n" + "=" * 110)
        print("PRE-REGISTERED HYPOTHESIS TESTING WITH HOLM-BONFERRONI CORRECTION (FWER alpha=0.05)")
        print("=" * 110)
        print(f"| {'Hypothesis Comparison':<38} | {'Baseline':<9} | {'C2 Full':<9} | {'Speedup':<9} | {'95% Bootstrap CI':<18} | {'Raw p':<9} | {'Holm p':<9} | {'Sig?':<5} |")
        print(f"|{'-'*40}|{'-'*11}|{'-'*11}|{'-'*11}|{'-'*20}|{'-'*11}|{'-'*11}|{'-'*7}|")
        for c in comparisons:
            b_str = f"{c['base_med']:.2f}"
            c_str = f"{c['c2_med']:.2f}"
            eff_str = f"{c['relative_speedup']*100:+.1f}%"
            ci_str = f"[{c['ci_low']*100:+.1f}%, {c['ci_high']*100:+.1f}%]"
            raw_p_str = f"{c['raw_p']:.4f}"
            holm_p_str = f"{c['holm_p']:.4f}"
            sig_str = "YES" if c["significant"] else "NO"
            print(f"| {c['hypothesis']:<38} | {b_str:<9} | {c_str:<9} | {eff_str:<9} | {ci_str:<18} | {raw_p_str:<9} | {holm_p_str:<9} | {sig_str:<5} |")
        print("=" * 110)

    # Outlier builds section: duration > 1.5 * cell median
    outliers: List[Dict[str, Any]] = []
    for (sc, ld, cn), recs in sorted(groups.items()):
        durations = [float(r["duration_wall_s"]) for r in recs if r.get("duration_wall_s") is not None]
        if not durations:
            continue
        med_dur = median(durations)
        threshold = 1.5 * med_dur
        for r in recs:
            dur = float(r.get("duration_wall_s", 0.0))
            if dur > threshold:
                tier = r.get("tier", "1")
                cycle = r.get("cycle", 1)
                blk = r.get("block_index", 1)
                claimed = r.get("claimed", False)
                decision = r.get("decision", "unknown")
                if not claimed and cn in ("c1-reactive", "c2-full", "c3-static"):
                    cause = "Unboosted / missed reservation under load"
                elif cycle == 1 and blk == 1:
                    cause = "Cold-start compiler pass / warm-up"
                elif r.get("contention_level", 0.0) > 90.0:
                    cause = "Extreme contention / CPU throttling"
                else:
                    cause = f"Variance under load ({dur:.2f}s vs med {med_dur:.2f}s)"
                outliers.append({
                    "tier": tier,
                    "scenario": sc,
                    "load": ld,
                    "condition": cn,
                    "block": blk,
                    "cycle": cycle,
                    "duration_s": dur,
                    "cell_med_s": med_dur,
                    "ratio": dur / med_dur if med_dur > 0 else 1.0,
                    "claimed": claimed,
                    "decision": decision,
                    "cause": cause,
                })

    print("\n" + "=" * 110)
    print("OUTLIER BUILDS (> 1.5x Median Duration of Condition-Load Cell)")
    print("=" * 110)
    if outliers:
        print(f"| {'Tier':<4} | {'Scen':<5} | {'Load':<8} | {'Condition':<12} | {'Blk':<3} | {'Cyc':<3} | {'Duration':<9} | {'Cell Med':<9} | {'Ratio':<6} | {'Cause':<30} |")
        print(f"|{'-'*6}|{'-'*7}|{'-'*10}|{'-'*14}|{'-'*5}|{'-'*5}|{'-'*11}|{'-'*11}|{'-'*8}|{'-'*32}|")
        for o in outliers:
            d_str = f"{o['duration_s']:.2f}s"
            m_str = f"{o['cell_med_s']:.2f}s"
            r_str = f"{o['ratio']:.2f}x"
            print(f"| {o['tier']:<4} | {o['scenario']:<5} | {o['load']:<8} | {o['condition']:<12} | {o['block']:<3} | {o['cycle']:<3} | {d_str:<9} | {m_str:<9} | {r_str:<6} | {o['cause']:<30} |")
    else:
        print("No outlier builds (> 1.5x median) detected across all cells.")
    print("=" * 110)

    # Boosted conditions CPU share per build
    boosted_recs = [
        r for r in records
        if r.get("condition") in ("c1-reactive", "c2-full", "c3-static")
        and r.get("cgroup_cpu_share") is not None
    ]
    if boosted_recs:
        print("\n" + "=" * 110)
        print("BOOSTED CONDITIONS: CPU SHARE PER BUILD")
        print("=" * 110)
        print(f"| {'Tier':<4} | {'Scen':<5} | {'Load':<8} | {'Condition':<12} | {'Blk':<3} | {'Cyc':<3} | {'Duration':<9} | {'CPU Share':<10} | {'Claim Lat':<10} | {'Claimed':<7} |")
        print(f"|{'-'*6}|{'-'*7}|{'-'*10}|{'-'*14}|{'-'*5}|{'-'*5}|{'-'*11}|{'-'*12}|{'-'*12}|{'-'*9}|")
        for r in boosted_recs:
            tier = r.get("tier", "1")
            sc = r.get("scenario", "A")
            ld = r.get("load_level", "high")
            cn = r.get("condition", "")
            blk = r.get("block_index", 1)
            cyc = r.get("cycle", 1)
            dur = f"{float(r.get('duration_wall_s', 0.0)):.2f}s"
            share = f"{float(r.get('cgroup_cpu_share', 0.0))*100:.1f}%"
            lat = f"{float(r.get('claim_latency_ms', 0.0)):.1f}ms" if r.get('claim_latency_ms') is not None else "N/A"
            clm = "YES" if r.get("claimed", False) else "NO"
            print(f"| {tier:<4} | {sc:<5} | {ld:<8} | {cn:<12} | {blk:<3} | {cyc:<3} | {dur:<9} | {share:<10} | {lat:<10} | {clm:<7} |")
        print("=" * 110)

    # Save CSV outputs if output directory specified
    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        metrics_csv = out_path / "summary_metrics.csv"
        with open(metrics_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "scenario", "load_level", "condition", "count", "med_wall_s", "mean_wall_s",
                "std_wall_s", "p25_wall_s", "p75_wall_s", "claims_count", "claim_rate",
                "med_claim_lat_ms", "mean_claim_lat_ms", "mean_cgroup_share",
            ])
            for m in metrics_summary:
                writer.writerow([
                    m["scenario"], m["load_level"], m["condition"], m["count"], m["med_wall_s"],
                    m["mean_wall_s"], m["std_wall_s"], m["p25_wall_s"], m["p75_wall_s"],
                    m["claims_count"], m["claim_rate"], m["med_claim_lat_ms"],
                    m["mean_claim_lat_ms"], m["mean_cgroup_share"],
                ])
        print(f"\nWrote metrics summary CSV: {metrics_csv}")

        if comparisons:
            hypo_csv = out_path / "hypothesis_tests.csv"
            with open(hypo_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "hypothesis", "scenario", "load_level", "comp_type", "base_med",
                    "c2_med", "relative_speedup", "ci_low", "ci_high", "raw_p", "holm_p", "significant",
                ])
                for c in comparisons:
                    writer.writerow([
                        c["hypothesis"], c["scenario"], c["load_level"], c["comp_type"],
                        c["base_med"], c["c2_med"], c["relative_speedup"], c["ci_low"],
                        c["ci_high"], c["raw_p"], c["holm_p"], c["significant"],
                    ])
            print(f"Wrote hypothesis tests CSV: {hypo_csv}")

    return {
        "metrics_summary": metrics_summary,
        "comparisons": comparisons,
    }


def main():
    parser = argparse.ArgumentParser(description="PreCompile Experiment Analysis")
    parser.add_argument("--jsonl", default=str(Path("benchmarks/results/results.jsonl")), help="Path to results JSONL")
    parser.add_argument("--output-dir", default=str(Path("benchmarks/results")), help="Directory to save CSV outputs")
    args = parser.parse_args()

    analyze_experiments(Path(args.jsonl), Path(args.output_dir))


if __name__ == "__main__":
    main()
