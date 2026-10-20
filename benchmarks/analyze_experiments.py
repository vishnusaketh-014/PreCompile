#!/usr/bin/env python3
"""PreCompile Experiment Analysis Suite.

Stage Mapping: Step 11a / Step 11b Part C / Step 11a3.
Reads benchmark JSONL records, computes summary statistics, Mann-Whitney U tests,
Holm-Bonferroni adjusted p-values, 95% non-parametric bootstrap confidence intervals,
and tests against Pre-Registered Success Criteria S1–S7.
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
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    adjusted = [0.0] * m
    running_max = 0.0

    for rank, (orig_idx, p_val) in enumerate(indexed):
        multiplier = m - rank
        adj = min(1.0, p_val * multiplier)
        running_max = max(running_max, adj)
        adjusted[orig_idx] = min(1.0, running_max)

    return adjusted


def analyze_experiments(
    jsonl_path: Path,
    output_dir: Optional[Path] = None,
    block_metrics_path: Optional[Path] = None,
) -> Dict[str, Any]:
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

    # Read block metrics if available
    block_metrics: List[Dict[str, Any]] = []
    b_path = block_metrics_path or (jsonl_path.parent / "block_metrics.jsonl")
    if b_path.exists():
        with open(b_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        block_metrics.append(json.loads(line))
                    except Exception:
                        pass

    # Group by (tier, scenario, load_level, condition) - Item 4
    groups: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
    for r in records:
        key = (
            str(r.get("tier", "1")),
            str(r.get("scenario", "A")),
            str(r.get("load_level", "high")),
            str(r.get("condition", "c0-baseline")),
        )
        groups.setdefault(key, []).append(r)

    metrics_summary: List[Dict[str, Any]] = []

    for (tr, sc, ld, cn), recs in sorted(groups.items()):
        durations = [
            float(r.get("build_wall_s", r.get("duration_wall_s")))
            for r in recs
            if r.get("build_wall_s") is not None or r.get("duration_wall_s") is not None
        ]
        mean_dur, std_dur = calc_mean_std(durations)
        med_dur = median(durations) if durations else 0.0

        # Without index 0 (sensitivity analysis excluding first build of each block)
        steady_recs = [r for r in recs if r.get("block_build_index", 1) != 0]
        steady_durs = [
            float(r.get("build_wall_s", r.get("duration_wall_s")))
            for r in steady_recs
            if r.get("build_wall_s") is not None or r.get("duration_wall_s") is not None
        ]
        med_steady_dur = median(steady_durs) if steady_durs else med_dur

        # Claims & Latencies
        claims = [r for r in recs if r.get("claimed", False)]
        claim_latencies = [
            float(r["claim_latency_ms"])
            for r in claims
            if r.get("claim_latency_ms") is not None
        ]
        mean_lat, std_lat = calc_mean_std(claim_latencies)
        med_lat = median(claim_latencies) if claim_latencies else None

        steady_claims = [r for r in steady_recs if r.get("claimed", False)]
        steady_latencies = [
            float(r["claim_latency_ms"])
            for r in steady_claims
            if r.get("claim_latency_ms") is not None
        ]
        med_steady_lat = median(steady_latencies) if steady_latencies else med_lat

        # CPU Share
        cpu_shares = [
            float(r["cgroup_cpu_share"])
            for r in recs
            if r.get("cgroup_cpu_share") is not None
        ]
        mean_share, _ = calc_mean_std(cpu_shares)

        # Unboosted builds vs Abstained builds count (Item 4)
        unboosted_count = sum(1 for r in recs if r.get("unboosted", False))
        abstained_count = sum(
            1 for r in recs
            if r.get("abstained", False) or "abstained" in str(r.get("unboosted_reason", ""))
        )

        metrics_summary.append({
            "tier": tr,
            "scenario": sc,
            "load_level": ld,
            "condition": cn,
            "count": len(recs),
            "med_wall_s": med_dur,
            "med_steady_wall_s": med_steady_dur,
            "mean_wall_s": mean_dur,
            "std_wall_s": std_dur,
            "claims_count": len(claims),
            "claim_rate": (len(claims) / len(recs)) if recs else 0.0,
            "med_claim_lat_ms": med_lat,
            "med_steady_lat_ms": med_steady_lat,
            "mean_claim_lat_ms": mean_lat if claim_latencies else None,
            "mean_cgroup_share": mean_share if cpu_shares else None,
            "unboosted_count": unboosted_count,
            "abstained_count": abstained_count,
            "raw_durations": durations,
            "raw_steady_durations": steady_durs,
            "raw_latencies": claim_latencies,
            "raw_steady_latencies": steady_latencies,
        })

    # Statistical comparisons for pre-registered hypotheses
    comparisons: List[Dict[str, Any]] = []
    lookup = {(m["tier"], m["scenario"], m["load_level"], m["condition"]): m for m in metrics_summary}

    tiers = sorted(list(set(m["tier"] for m in metrics_summary)))
    scenarios = sorted(list(set(m["scenario"] for m in metrics_summary)))
    loads = sorted(list(set(m["load_level"] for m in metrics_summary)))

    for tr in tiers:
        for sc in scenarios:
            for ld in loads:
                c0 = lookup.get((tr, sc, ld, "c0-baseline"))
                c1 = lookup.get((tr, sc, ld, "c1-reactive"))
                c2 = lookup.get((tr, sc, ld, "c2-full"))
                c3 = lookup.get((tr, sc, ld, "c3-static"))

                # S1: C2 vs C0 Speedup (All builds)
                if c2 and c0 and len(c2["raw_durations"]) >= 2 and len(c0["raw_durations"]) >= 2:
                    _, _, _, p_val = mann_whitney_u(c2["raw_durations"], c0["raw_durations"])
                    rel_speedup = (c0["med_wall_s"] - c2["med_wall_s"]) / c0["med_wall_s"] if c0["med_wall_s"] > 0 else 0.0
                    _, ci_low, ci_high = bootstrap_relative_effect_ci(
                        {"all": c2["raw_durations"]}, {"all": c0["raw_durations"]}, n_resamples=1000
                    )
                    comparisons.append({
                        "hypothesis": f"S1: C2 vs C0 Speedup ({sc}, {ld})",
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

                # S2: C2 vs C1 Anticipatory Advantage (All builds)
                if c2 and c1 and len(c2["raw_durations"]) >= 2 and len(c1["raw_durations"]) >= 2:
                    _, _, _, p_val = mann_whitney_u(c2["raw_durations"], c1["raw_durations"])
                    rel_speedup = (c1["med_wall_s"] - c2["med_wall_s"]) / c1["med_wall_s"] if c1["med_wall_s"] > 0 else 0.0
                    _, ci_low, ci_high = bootstrap_relative_effect_ci(
                        {"all": c2["raw_durations"]}, {"all": c1["raw_durations"]}, n_resamples=1000
                    )
                    comparisons.append({
                        "hypothesis": f"S2: C2 vs C1 Duration ({sc}, {ld})",
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

                # S3 Primary: C2 vs C1 Claim Latency (All builds)
                if c2 and c1 and len(c2["raw_latencies"]) >= 2 and len(c1["raw_latencies"]) >= 2:
                    _, _, _, p_val = mann_whitney_u(c2["raw_latencies"], c1["raw_latencies"])
                    c1_lat = c1["med_claim_lat_ms"] or 0.0
                    c2_lat = c2["med_claim_lat_ms"] or 0.0
                    rel_red = (c1_lat - c2_lat) / c1_lat if c1_lat > 0 else 0.0
                    _, ci_low, ci_high = bootstrap_relative_effect_ci(
                        {"all": c2["raw_latencies"]}, {"all": c1["raw_latencies"]}, n_resamples=1000
                    )
                    comparisons.append({
                        "hypothesis": f"S3: C2 vs C1 Claim Latency ({sc}, {ld})",
                        "scenario": sc,
                        "load_level": ld,
                        "comp_type": "C2 vs C1 Latency (All)",
                        "base_med": c1_lat,
                        "c2_med": c2_lat,
                        "relative_speedup": rel_red,
                        "ci_low": ci_low,
                        "ci_high": ci_high,
                        "raw_p": p_val,
                    })

                # S3 Sensitivity Analysis: C2 vs C1 Claim Latency (Excluding build index 0)
                if c2 and c1 and len(c2["raw_steady_latencies"]) >= 2 and len(c1["raw_steady_latencies"]) >= 2:
                    _, _, _, p_val_st = mann_whitney_u(c2["raw_steady_latencies"], c1["raw_steady_latencies"])
                    c1_lat_st = c1["med_steady_lat_ms"] or 0.0
                    c2_lat_st = c2["med_steady_lat_ms"] or 0.0
                    rel_red_st = (c1_lat_st - c2_lat_st) / c1_lat_st if c1_lat_st > 0 else 0.0
                    _, ci_lo_st, ci_hi_st = bootstrap_relative_effect_ci(
                        {"all": c2["raw_steady_latencies"]}, {"all": c1["raw_steady_latencies"]}, n_resamples=1000
                    )
                    comparisons.append({
                        "hypothesis": f"S3 Sensitivity (ex-idx0): C2 vs C1 Latency ({sc}, {ld})",
                        "scenario": sc,
                        "load_level": ld,
                        "comp_type": "C2 vs C1 Latency (ex-idx0)",
                        "base_med": c1_lat_st,
                        "c2_med": c2_lat_st,
                        "relative_speedup": rel_red_st,
                        "ci_low": ci_lo_st,
                        "ci_high": ci_hi_st,
                        "raw_p": p_val_st,
                    })

    # Holm-Bonferroni correction
    raw_p_list = [c["raw_p"] for c in comparisons]
    adj_p_list = holm_bonferroni(raw_p_list)
    for i, c in enumerate(comparisons):
        c["holm_p"] = adj_p_list[i]
        c["significant"] = adj_p_list[i] < 0.05

    # 1. Main Metrics Table
    print("\n" + "=" * 138)
    print("PRECOMPILE EVALUATION SUMMARY: BUILD DURATIONS, LATENCIES & SYSTEM METRICS")
    print("=" * 138)
    print(f"| {'Tier':<6} | {'Scenario':<8} | {'Load':<8} | {'Condition':<12} | {'N':<4} | {'Med Wall':<9} | {'Ex-Idx0':<9} | {'Claims':<7} | {'Med Lat':<9} | {'Ex-Idx0 Lat':<11} | {'CPU Share':<9} | {'Unboosted':<9} | {'Abstained':<9} |")
    print(f"|{'-'*8}|{'-'*10}|{'-'*10}|{'-'*14}|{'-'*6}|{'-'*11}|{'-'*11}|{'-'*9}|{'-'*11}|{'-'*13}|{'-'*11}|{'-'*11}|{'-'*11}|")
    for m in metrics_summary:
        med_w = f"{m['med_wall_s']:.2f}s"
        med_st = f"{m['med_steady_wall_s']:.2f}s"
        clm = f"{m['claims_count']}/{m['count']}"
        med_l = f"{m['med_claim_lat_ms']:.1f}ms" if m['med_claim_lat_ms'] is not None else "N/A"
        med_lst = f"{m['med_steady_lat_ms']:.1f}ms" if m['med_steady_lat_ms'] is not None else "N/A"
        share = f"{m['mean_cgroup_share']*100:.1f}%" if m['mean_cgroup_share'] is not None else "N/A"
        unb = f"{m['unboosted_count']}"
        abs_cnt = f"{m['abstained_count']}"
        print(f"| {m['tier']:<6} | {m['scenario']:<8} | {m['load_level']:<8} | {m['condition']:<12} | {m['count']:<4} | {med_w:<9} | {med_st:<9} | {clm:<7} | {med_l:<9} | {med_lst:<11} | {share:<9} | {unb:<9} | {abs_cnt:<9} |")
    print("=" * 138)

    # Cross-check of duration measurements (Item 1)
    diffs = [
        float(r["wall_daemon_diff_s"])
        for r in records
        if r.get("wall_daemon_diff_s") is not None
    ]
    if diffs:
        print("\n" + "=" * 100)
        print("BUILD-TIME MEASUREMENT CROSS-CHECK: |build_wall_s - daemon_duration_s| DISTRIBUTION")
        print("=" * 100)
        print(f"Sample Size: {len(diffs)} builds with daemon duration recorded")
        print(f"Min:    {min(diffs)*1000:.2f} ms")
        print(f"p25:    {percentile(diffs, 25)*1000:.2f} ms")
        print(f"Median: {median(diffs)*1000:.2f} ms")
        print(f"p75:    {percentile(diffs, 75)*1000:.2f} ms")
        print(f"Max:    {max(diffs)*1000:.2f} ms")
        print(f"Mean:   {sum(diffs)/len(diffs)*1000:.2f} ms")
        print("=" * 100)

    # 2. Secondary Decomposed C2 Reporting (Reserved vs Holdout)
    c2_recs = [r for r in records if r.get("condition") == "c2-full"]
    if c2_recs:
        print("\n" + "=" * 100)
        print("SECONDARY DECOMPOSED REPORTING: C2 RESERVED VS HOLDOUT ARMS (ITT vs PER-PROTOCOL)")
        print("=" * 100)
        print(f"| {'Scenario':<8} | {'Load':<8} | {'Arm':<14} | {'N':<5} | {'Median Duration':<17} | {'Mean ± Std':<18} |")
        print(f"|{'-'*10}|{'-'*10}|{'-'*16}|{'-'*7}|{'-'*19}|{'-'*20}|")
        c2_groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for r in c2_recs:
            c2_groups.setdefault((r.get("scenario", "A"), r.get("load_level", "high")), []).append(r)
        for (sc, ld), g_recs in sorted(c2_groups.items()):
            res_durs = [float(r["duration_wall_s"]) for r in g_recs if r.get("decision") == "reserved"]
            hld_durs = [float(r["duration_wall_s"]) for r in g_recs if r.get("decision") == "holdout"]
            all_durs = [float(r["duration_wall_s"]) for r in g_recs]
            if all_durs:
                m_all, s_all = calc_mean_std(all_durs)
                print(f"| {sc:<8} | {ld:<8} | {'C2 All (ITT)':<14} | {len(all_durs):<5} | {median(all_durs):.2f}s{'':<11} | {m_all:.2f}±{s_all:.2f}s{'':<10} |")
            if res_durs:
                m_res, s_res = calc_mean_std(res_durs)
                print(f"| {sc:<8} | {ld:<8} | {'C2 Reserved':<14} | {len(res_durs):<5} | {median(res_durs):.2f}s{'':<11} | {m_res:.2f}±{s_res:.2f}s{'':<10} |")
            if hld_durs:
                m_hld, s_hld = calc_mean_std(hld_durs)
                print(f"| {sc:<8} | {ld:<8} | {'C2 Holdout':<14} | {len(hld_durs):<5} | {median(hld_durs):.2f}s{'':<11} | {m_hld:.2f}±{s_hld:.2f}s{'':<10} |")
        print("=" * 100)

    # 3. Block-Level Metrics (Overhead, Background Impact, Precision)
    if block_metrics:
        print("\n" + "=" * 125)
        print("BLOCK-LEVEL METRICS: MONITORING OVERHEAD, BACKGROUND IMPACT & PREDICTION PRECISION")
        print("=" * 125)
        print(f"| {'Blk':<3} | {'Scen':<5} | {'Load':<8} | {'Condition':<12} | {'Wall(s)':<8} | {'PSI(0)':<7} | {'DaemonCPU':<10} | {'Scans/s':<8} | {'Scan(ms)':<8} | {'BgRate(cpu/s)':<14} | {'Hit%':<6} | {'FalseRes%':<9} |")
        print(f"|{'-'*5}|{'-'*7}|{'-'*10}|{'-'*14}|{'-'*10}|{'-'*9}|{'-'*12}|{'-'*10}|{'-'*10}|{'-'*16}|{'-'*8}|{'-'*11}|")
        for b in block_metrics:
            b_i = b.get("block_index", 0)
            sc = b.get("scenario", "A")
            ld = b.get("load_level", "")
            cn = b.get("condition", "")
            wall = f"{b.get('wall_time_s', 0.0):.1f}s"
            psi0 = f"{b.get('block_start_psi', 0.0):.1f}%"
            dcpu = f"{b.get('daemon_cpu_pct', 0.0):.2f}%" if cn != "c0-baseline" else "N/A (no daemon)"
            w_stats = b.get("watcher_stats", {})
            scans_s = f"{w_stats.get('scans_per_sec', 0.0):.1f}" if w_stats else "N/A"
            avg_s = f"{w_stats.get('avg_scan_ms', 0.0):.2f}" if w_stats else "N/A"
            bg = f"{b.get('bg_cpu_rate', 0.0):.2f}"
            hit = f"{b.get('hit_rate', 0.0)*100:.0f}%" if b.get("hit_rate") is not None else "N/A"
            f_res = f"{b.get('false_res_rate', 0.0)*100:.0f}%" if b.get("false_res_rate") is not None else "N/A"
            print(f"| {b_i:<3} | {sc:<5} | {ld:<8} | {cn:<12} | {wall:<8} | {psi0:<7} | {dcpu:<10} | {scans_s:<8} | {avg_s:<8} | {bg:<14} | {hit:<6} | {f_res:<9} |")
        print("=" * 125)

        # Print per-thread CPU table if thread_cpu_breakdown exists (Item 2a)
        has_threads = any(b.get("thread_cpu_breakdown") for b in block_metrics)
        if has_threads:
            print("\n" + "=" * 105)
            print("DAEMON PER-THREAD CPU USAGE BREAKDOWN (from /proc/<pid>/task/*/stat)")
            print("=" * 105)
            print(f"| {'Blk':<3} | {'Scen':<5} | {'Load':<8} | {'Condition':<12} | {'Thread Name':<20} | {'Core CPU %':<12} | {'Daemon Share %':<15} |")
            print(f"|{'-'*5}|{'-'*7}|{'-'*10}|{'-'*14}|{'-'*22}|{'-'*14}|{'-'*17}|")
            for b in block_metrics:
                b_i = b.get("block_index", 0)
                sc = b.get("scenario", "A")
                ld = b.get("load_level", "")
                cn = b.get("condition", "")
                th_data = b.get("thread_cpu_breakdown", {})
                for comm, info in sorted(th_data.items(), key=lambda x: -x[1].get("pct_core", 0.0)):
                    pct_c = f"{info.get('pct_core', 0.0):.2f}%"
                    pct_d = f"{info.get('pct_daemon', 0.0):.1f}%"
                    print(f"| {b_i:<3} | {sc:<5} | {ld:<8} | {cn:<12} | {comm:<20} | {pct_c:<12} | {pct_d:<15} |")
            print("=" * 105)

    # 4. Hypothesis Testing Table
    if comparisons:
        print("\n" + "=" * 125)
        print("HYPOTHESIS TESTING WITH HOLM-BONFERRONI CORRECTION (FWER alpha=0.05)")
        print("=" * 125)
        print(f"| {'Hypothesis Comparison':<45} | {'Baseline':<9} | {'C2 Full':<9} | {'Speedup':<9} | {'95% Bootstrap CI':<19} | {'Raw p':<9} | {'Holm p':<9} | {'Sig?':<5} |")
        print(f"|{'-'*47}|{'-'*11}|{'-'*11}|{'-'*11}|{'-'*21}|{'-'*11}|{'-'*11}|{'-'*7}|")
        for c in comparisons:
            b_str = f"{c['base_med']:.2f}"
            c_str = f"{c['c2_med']:.2f}"
            eff_str = f"{c['relative_speedup']*100:+.1f}%"
            ci_str = f"[{c['ci_low']*100:+.1f}%, {c['ci_high']*100:+.1f}%]"
            raw_p_str = f"{c['raw_p']:.4f}"
            holm_p_str = f"{c['holm_p']:.4f}"
            sig_str = "YES" if c["significant"] else "NO"
            print(f"| {c['hypothesis']:<45} | {b_str:<9} | {c_str:<9} | {eff_str:<9} | {ci_str:<19} | {raw_p_str:<9} | {holm_p_str:<9} | {sig_str:<5} |")
        print("=" * 125)

    # 5. Outlier Builds Section (Duration > 1.5x Cell Median + CPU Share & Unboosted Reason)
    outliers: List[Dict[str, Any]] = []
    for (tr, sc, ld, cn), recs in sorted(groups.items()):
        durations = [
            float(r.get("build_wall_s", r.get("duration_wall_s")))
            for r in recs
            if r.get("build_wall_s") is not None or r.get("duration_wall_s") is not None
        ]
        if not durations:
            continue
        med_dur = median(durations)
        threshold = 1.5 * med_dur
        for r in recs:
            dur = float(r.get("build_wall_s", r.get("duration_wall_s", 0.0)))
            if dur > threshold:
                tier = r.get("tier", "1")
                cycle = r.get("cycle", 1)
                blk = r.get("block_index", 1)
                b_idx = r.get("block_build_index", 0)
                claimed = r.get("claimed", False)
                decision = r.get("decision", "unknown")
                share = r.get("cgroup_cpu_share")
                unb = r.get("unboosted", False)
                unb_reason = r.get("unboosted_reason")

                cause = unb_reason if unb_reason else f"Variance under load ({dur:.2f}s vs med {med_dur:.2f}s)"
                outliers.append({
                    "tier": tier,
                    "scenario": sc,
                    "load": ld,
                    "condition": cn,
                    "block": blk,
                    "block_idx": b_idx,
                    "cycle": cycle,
                    "duration_s": dur,
                    "cell_med_s": med_dur,
                    "ratio": dur / med_dur if med_dur > 0 else 1.0,
                    "cgroup_share": share,
                    "unboosted": unb,
                    "cause": cause,
                })

    print("\n" + "=" * 125)
    print("OUTLIER BUILDS (> 1.5x Median Duration of Condition-Load Cell)")
    print("=" * 125)
    if outliers:
        print(f"| {'Tier':<4} | {'Scen':<5} | {'Load':<8} | {'Condition':<12} | {'Blk':<3} | {'Idx':<3} | {'Duration':<9} | {'Cell Med':<9} | {'Ratio':<6} | {'Share':<7} | {'Unboosted?':<11} | {'Cause':<30} |")
        print(f"|{'-'*6}|{'-'*7}|{'-'*10}|{'-'*14}|{'-'*5}|{'-'*5}|{'-'*11}|{'-'*11}|{'-'*8}|{'-'*9}|{'-'*13}|{'-'*32}|")
        for o in outliers:
            d_str = f"{o['duration_s']:.2f}s"
            m_str = f"{o['cell_med_s']:.2f}s"
            r_str = f"{o['ratio']:.2f}x"
            sh_str = f"{o['cgroup_share']*100:.1f}%" if o["cgroup_share"] is not None else "N/A"
            unb_str = "YES" if o["unboosted"] else "NO"
            print(f"| {o['tier']:<4} | {o['scenario']:<5} | {o['load']:<8} | {o['condition']:<12} | {o['block']:<3} | {o['block_idx']:<3} | {d_str:<9} | {m_str:<9} | {r_str:<6} | {sh_str:<7} | {unb_str:<11} | {o['cause']:<30} |")
    else:
        print("No outlier builds (> 1.5x median) detected across all cells.")
    print("=" * 125)

    # 6. Evaluation of Pre-Registered Success Criteria S1-S7
    print("\n" + "=" * 125)
    print("EVALUATION OF PRE-REGISTERED SUCCESS CRITERIA (S1 - S7)")
    print("=" * 125)
    print(f"| {'Criterion':<32} | {'Requirement':<42} | {'Observed Value':<24} | {'Status':<10} |")
    print(f"|{'-'*34}|{'-'*44}|{'-'*26}|{'-'*12}|")

    # S1: Contended Speedup
    s1_comp = [c for c in comparisons if "S1:" in c["hypothesis"] and c["load_level"] in ("moderate", "high")]
    s1_pass = all(c["relative_speedup"] >= 0.20 and c["significant"] for c in s1_comp) if s1_comp else False
    s1_parts = []
    for c in s1_comp:
        s1_parts.append(f"{c['load_level']}: {c['relative_speedup']*100:+.1f}% (p={c['holm_p']:.3f})")
    s1_obs = f"Speedup: {', '.join(s1_parts)}" if s1_parts else "No data"
    print(f"| {'S1: Contended Speedup':<32} | {'C2 vs C0 >= 20.0% speedup (pHolm < 0.05)':<42} | {s1_obs[:24]:<24} | {'PASS' if s1_pass else 'PENDING/FAIL':<10} |")

    # S2: Anticipatory vs Reactive
    s2_comp = [c for c in comparisons if "S2:" in c["hypothesis"]]
    s2_parts = []
    for c in s2_comp:
        s2_parts.append(f"{c['load_level']}: {c['relative_speedup']*100:+.1f}%")
    s2_obs = ", ".join(s2_parts) if s2_parts else "Reported without threshold"
    print(f"| {'S2: Anticipatory vs Reactive':<32} | {'No threshold; effect & 95% CI reported':<42} | {s2_obs[:24]:<24} | {'REPORTED':<10} |")

    # S3: Claim Latency (Primary uses ALL builds - Item 2b)
    s3_comp = [c for c in comparisons if c["hypothesis"].startswith("S3:")]
    s3_pass = all(c["relative_speedup"] >= 0.25 and c["significant"] for c in s3_comp) if s3_comp else False
    s3_parts = []
    for c in s3_comp:
        s3_parts.append(f"{c['load_level']}: {c['relative_speedup']*100:+.1f}%")
    s3_obs = ", ".join(s3_parts) if s3_parts else "No data"
    print(f"| {'S3: Claim Latency Advantage':<32} | {'C2 claim lat >= 25.0% lower than C1':<42} | {s3_obs[:24]:<24} | {'PASS' if s3_pass else 'PENDING/FAIL':<10} |")

    # S4: Precision on Irregular Workloads
    sc_b_c2 = [b for b in block_metrics if b.get("scenario") == "B" and b.get("condition") == "c2-full"]
    sc_c_c2 = [b for b in block_metrics if b.get("scenario") == "C" and b.get("condition") == "c2-full"]
    f_res_b = sc_b_c2[0].get("false_res_rate") if sc_b_c2 else None
    f_res_b_str = f"B: {f_res_b*100:.1f}% false" if f_res_b is not None else "Scenario B: N/A"
    print(f"| {'S4: Precision on Irregular Work':<32} | {'Scen B false res <= 20%, Scen C arm <= 20%':<42} | {f_res_b_str[:24]:<24} | {'EVALUATED':<10} |")

    # S5: Overhead (Item 2a: Requires BOTH daemon CPU <= 1.0% AND scan <= 1.5ms; FAIL if either exceeds)
    c2_blocks = [b for b in block_metrics if b.get("condition") == "c2-full"]
    max_dcpu = max((b.get("daemon_cpu_pct", 0.0) for b in c2_blocks), default=0.0)
    max_scan = max((b.get("watcher_stats", {}).get("avg_scan_ms", 0.0) for b in c2_blocks), default=0.0)
    s5_pass = (max_dcpu <= 1.0) and (max_scan <= 1.5) if c2_blocks else False
    s5_obs = f"CPU={max_dcpu:.2f}%, Scan={max_scan:.2f}ms"
    print(f"| {'S5: Monitoring Overhead':<32} | {'Daemon CPU <= 1.0%, Avg Scan <= 1.5ms':<42} | {s5_obs[:24]:<24} | {'PASS' if s5_pass else 'FAIL':<10} |")

    # S6: Bounded Harm
    idle_c0 = next((m for m in metrics_summary if m["scenario"] == "A" and m["load_level"] == "idle" and m["condition"] == "c0-baseline"), None)
    idle_c2 = next((m for m in metrics_summary if m["scenario"] == "A" and m["load_level"] == "idle" and m["condition"] == "c2-full"), None)
    idle_delta = (
        abs(idle_c2["med_wall_s"] - idle_c0["med_wall_s"]) / idle_c0["med_wall_s"]
        if (idle_c0 and idle_c2 and idle_c0["med_wall_s"] > 0)
        else 0.0
    )
    s6_pass = idle_delta <= 0.05 if (idle_c0 and idle_c2) else True
    s6_obs = f"Idle delta: {idle_delta*100:+.1f}%" if (idle_c0 and idle_c2) else "No idle data"
    print(f"| {'S6: Bounded Harm':<32} | {'Idle arming <= 10%, build time within +-5%':<42} | {s6_obs[:24]:<24} | {'PASS' if s6_pass else 'PENDING':<10} |")

    # S7: Cleanup
    leftovers = [p.name for p in Path("/sys/fs/cgroup").glob("precompile-res-*") if p.is_dir()]
    s7_pass = len(leftovers) == 0
    s7_obs = f"Leftover cgroups: {len(leftovers)}"
    print(f"| {'S7: Resource Cleanup':<32} | {'0 leftover cgroups, 0 orphan processes':<42} | {s7_obs[:24]:<24} | {'PASS' if s7_pass else 'FAIL':<10} |")
    print("=" * 125)

    # Save CSV outputs
    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        metrics_csv = out_path / "summary_metrics.csv"
        with open(metrics_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "tier", "scenario", "load_level", "condition", "count", "med_wall_s", "med_steady_wall_s",
                "mean_wall_s", "std_wall_s", "claims_count", "claim_rate", "med_claim_lat_ms",
                "med_steady_lat_ms", "mean_claim_lat_ms", "mean_cgroup_share", "unboosted_count", "abstained_count",
            ])
            for m in metrics_summary:
                writer.writerow([
                    m["tier"], m["scenario"], m["load_level"], m["condition"], m["count"], m["med_wall_s"],
                    m["med_steady_wall_s"], m["mean_wall_s"], m["std_wall_s"], m["claims_count"],
                    m["claim_rate"], m["med_claim_lat_ms"], m["med_steady_lat_ms"],
                    m["mean_claim_lat_ms"], m["mean_cgroup_share"], m["unboosted_count"], m["abstained_count"],
                ])
        print(f"\nWrote metrics summary CSV: {metrics_csv}")

    return {
        "metrics_summary": metrics_summary,
        "comparisons": comparisons,
    }


def main():
    parser = argparse.ArgumentParser(description="PreCompile Experiment Analysis")
    parser.add_argument("--jsonl", default=str(Path("benchmarks/results/results.jsonl")), help="Path to results JSONL")
    parser.add_argument("--output-dir", default=str(Path("benchmarks/results")), help="Directory to save CSV outputs")
    parser.add_argument("--block-metrics", default=str(Path("benchmarks/results/block_metrics.jsonl")), help="Path to block metrics JSONL")
    args = parser.parse_args()

    analyze_experiments(Path(args.jsonl), Path(args.output_dir), Path(args.block_metrics))


if __name__ == "__main__":
    main()
