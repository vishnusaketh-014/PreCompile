#!/usr/bin/env python3
"""Analysis script for A/B Smoke Test results.

Computes condition breakdown (median, p95, mean), Mann-Whitney U test (pure Python),
claim latency statistics, and leftover cgroup verification.
"""

import argparse
import math
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def percentile(data: List[float], p: float) -> float:
    """Compute p-th percentile [0..100] using linear interpolation."""
    if not data:
        return 0.0
    s = sorted(data)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    d0 = s[int(f)] * (c - k)
    d1 = s[int(c)] * (k - f)
    return d0 + d1


def mann_whitney_u(x: List[float], y: List[float]) -> Tuple[float, float, float, float]:
    """Pure-Python Mann-Whitney U test with normal approximation and tie correction.

    Returns:
        (u_stat, u_min, z_score, p_value)
    """
    n1 = len(x)
    n2 = len(y)
    if n1 == 0 or n2 == 0:
        return 0.0, 0.0, 0.0, 1.0

    # Combine observations with sample origin tag (1 for x, 2 for y)
    combined = [(val, 1) for val in x] + [(val, 2) for val in y]
    combined.sort(key=lambda t: t[0])

    # Assign ranks with tie handling (average rank for equal values)
    ranks: List[Tuple[float, int]] = []
    i = 0
    total = len(combined)
    tie_counts: List[int] = []

    while i < total:
        j = i
        while j < total - 1 and combined[j][0] == combined[j + 1][0]:
            j += 1
        num_ties = j - i + 1
        avg_rank = (i + 1 + j + 1) / 2.0
        if num_ties > 1:
            tie_counts.append(num_ties)
        for k in range(i, j + 1):
            ranks.append((avg_rank, combined[k][1]))
        i = j + 1

    # Sum ranks for sample 1 (x)
    r1 = sum(r for r, sample_id in ranks if sample_id == 1)
    u1 = r1 - (n1 * (n1 + 1)) / 2.0
    u2 = (n1 * n2) - u1
    u = min(u1, u2)

    mean_u = (n1 * n2) / 2.0

    # Tie correction for variance
    n = n1 + n2
    tie_term = sum((t**3 - t) for t in tie_counts)
    if n > 1:
        tie_correction = 1.0 - (tie_term / (n**3 - n))
    else:
        tie_correction = 1.0

    var_u = (n1 * n2 / 12.0) * ((n + 1) * tie_correction)
    std_u = math.sqrt(max(1e-9, var_u))

    z = (u1 - mean_u) / std_u
    # Two-sided p-value from standard normal CDF
    p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2.0))))

    return u1, u, z, p


def analyze_db(db_path: Path) -> None:
    """Analyze builds and reservations stored in db_path."""
    if not db_path.exists():
        print(f"Error: Database {db_path} does not exist.")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Query builds
    cur.execute(
        """
        SELECT condition, duration_s
        FROM builds
        ORDER BY id ASC
        """
    )
    rows = cur.fetchall()

    by_condition: Dict[str, List[float]] = {}
    for cond, dur in rows:
        by_condition.setdefault(cond, []).append(dur)

    print("\n" + "=" * 70)
    print("A/B SMOKE TEST: BUILD DURATION BY CONDITION")
    print("=" * 70)
    print(f"{'Condition':<12} | {'N':>4} | {'Median (s)':>10} | {'P95 (s)':>10} | {'Mean (s)':>10}")
    print("-" * 58)

    for cond in ["reserved", "holdout", "observed"]:
        if cond in by_condition:
            durs = by_condition[cond]
            n = len(durs)
            med = percentile(durs, 50)
            p95 = percentile(durs, 95)
            mean_val = sum(durs) / n
            print(f"{cond:<12} | {n:>4} | {med:>10.3f} | {p95:>10.3f} | {mean_val:>10.3f}")

    # Mann-Whitney U test between reserved and holdout
    reserved_durs = by_condition.get("reserved", [])
    holdout_durs = by_condition.get("holdout", [])

    print("\n" + "=" * 70)
    print("STATISTICAL SIGNIFICANCE (Mann-Whitney U Test)")
    print("=" * 70)
    if reserved_durs and holdout_durs:
        u1, u, z, p = mann_whitney_u(reserved_durs, holdout_durs)
        med_res = percentile(reserved_durs, 50)
        med_hld = percentile(holdout_durs, 50)
        speedup = ((med_hld - med_res) / med_hld) * 100.0 if med_hld > 0 else 0.0
        print(f"  Reserved samples (N1):  {len(reserved_durs)}")
        print(f"  Holdout samples  (N2):  {len(holdout_durs)}")
        print(f"  Reserved median:       {med_res:.3f} s")
        print(f"  Holdout median:        {med_hld:.3f} s")
        print(f"  Relative speedup:      {speedup:.1f}% faster")
        print(f"  Mann-Whitney U:        {u1:.1f} (min U: {u:.1f})")
        print(f"  Z-score:               {z:.4f}")
        print(f"  p-value (two-sided):   {p:.4e}")
        if p < 0.05:
            print("  Result:                STATISTICALLY SIGNIFICANT (p < 0.05)")
        else:
            print("  Result:                NOT STATISTICALLY SIGNIFICANT (p >= 0.05)")
    else:
        print("  Insufficient samples in reserved or holdout groups to perform test.")

    # Query reservations
    cur.execute(
        """
        SELECT id, final_state, claim_latency_s
        FROM reservations
        ORDER BY rowid ASC
        """
    )
    res_rows = cur.fetchall()

    claimed_latencies = [r[2] for r in res_rows if r[2] is not None and r[1] == "CLOSED"]
    expired_count = sum(1 for r in res_rows if r[1] == "EXPIRED")
    closed_count = sum(1 for r in res_rows if r[1] == "CLOSED")
    total_res = len(res_rows)

    print("\n" + "=" * 70)
    print("RESERVATION CLAIM LATENCY & EFFICIENCY")
    print("=" * 70)
    print(f"  Total reservations created: {total_res}")
    print(f"  Claimed & CLOSED:          {closed_count}")
    print(f"  EXPIRED (unclaimed):       {expired_count}")
    if claimed_latencies:
        mean_lat = sum(claimed_latencies) / len(claimed_latencies)
        med_lat = percentile(claimed_latencies, 50)
        p95_lat = percentile(claimed_latencies, 95)
        print(f"  Mean claim latency:        {mean_lat * 1000.0:.2f} ms")
        print(f"  Median claim latency:      {med_lat * 1000.0:.2f} ms")
        print(f"  P95 claim latency:         {p95_lat * 1000.0:.2f} ms")

    # Leftover cgroup check
    cgroup_root = Path("/sys/fs/cgroup")
    leftovers = []
    if cgroup_root.exists():
        for d in cgroup_root.iterdir():
            if d.is_dir() and d.name.startswith("precompile-res-"):
                leftovers.append(d.name)

    print("\n" + "=" * 70)
    print("LEFTOVER CGROUP DIRECTORY CHECK")
    print("=" * 70)
    if leftovers:
        print(f"  FAIL: Found {len(leftovers)} leftover cgroup directories: {leftovers}")
    else:
        print("  PASS: Exactly 0 leftover precompile-res-* cgroup directories in /sys/fs/cgroup.")
    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Analyze A/B Smoke Test results")
    parser.add_argument("--db", type=Path, required=True, help="Path to PreCompile SQLite database")
    args = parser.parse_args()
    analyze_db(args.db)


if __name__ == "__main__":
    main()
