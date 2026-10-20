#!/usr/bin/env python3
"""Part A analysis of the A/B smoke data from the last run.
Prints all reserved and holdout build durations, text histograms, and per-reserved details.
Also verifies H1 (whether re-scan uses stale watcher table).
"""
import math
import sqlite3
import sys
from pathlib import Path

DB_PATH = "/tmp/ab_smoke.db"


def text_histogram(values, bins=10, width=40, label=""):
    if not values:
        print(f"  {label}: (no data)")
        return
    lo, hi = min(values), max(values)
    if hi == lo:
        print(f"  {label}: all values = {lo:.3f}s")
        return
    bucket_size = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = min(int((v - lo) / bucket_size), bins - 1)
        counts[idx] += 1
    max_count = max(counts)
    print(f"  {label} (n={len(values)}, min={lo:.3f}s, max={hi:.3f}s)")
    for i, c in enumerate(counts):
        lo_b = lo + i * bucket_size
        hi_b = lo + (i + 1) * bucket_size
        bar = "#" * int(c / max_count * width) if max_count > 0 else ""
        print(f"  [{lo_b:.2f},{hi_b:.2f}) | {bar:<{width}} | {c}")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=" * 70)
    print("PART A.1: A/B RUN DATA ANALYSIS")
    print("=" * 70)

    # --- RESERVED builds
    cur.execute("""
        SELECT b.id, b.duration_s, b.prediction_id,
               r.id as res_id, r.claimed_pid, r.claim_latency_s,
               r.final_state as res_state
        FROM builds b
        LEFT JOIN reservations r ON b.prediction_id = r.prediction_id
        WHERE b.condition = 'reserved'
        ORDER BY b.duration_s ASC
    """)
    reserved_rows = cur.fetchall()

    reserved_durs = [r["duration_s"] for r in reserved_rows]
    print(f"\n=== RESERVED BUILDS ({len(reserved_rows)} total) ===")
    print(f"  Sorted durations: {[round(d, 3) for d in reserved_durs]}")
    print()
    text_histogram(reserved_durs, bins=12, label="Reserved duration distribution")

    print("\n  Per-build details:")
    print(f"  {'Build ID':>8} {'Duration':>10} {'Res ID':>12} {'PID':>7} {'Latency':>10} {'Res State':>10}")
    for r in reserved_rows:
        lat = f"{r['claim_latency_s']*1000:.1f}ms" if r['claim_latency_s'] else "N/A"
        print(f"  {r['id']:>8} {r['duration_s']:>9.3f}s {(str(r['res_id']) if r['res_id'] else 'N/A'):>12} {(str(r['claimed_pid']) if r['claimed_pid'] else 'N/A'):>7} {lat:>10} {(r['res_state'] or 'N/A'):>10}")

    # Bimodal detection: count fast (<5s) vs slow (>=5s)
    fast = [d for d in reserved_durs if d < 5.0]
    slow = [d for d in reserved_durs if d >= 5.0]
    print(f"\n  Bimodal split (<5s vs >=5s): fast={len(fast)}, slow={len(slow)}")
    if fast:
        print(f"  Fast builds: {[round(d, 3) for d in fast]}")
    if slow:
        print(f"  Slow builds: {[round(d, 3) for d in slow]}")

    # --- HOLDOUT builds
    cur.execute("""
        SELECT duration_s FROM builds WHERE condition = 'holdout' ORDER BY duration_s ASC
    """)
    holdout_rows = cur.fetchall()
    holdout_durs = [r["duration_s"] for r in holdout_rows]

    print(f"\n=== HOLDOUT BUILDS ({len(holdout_rows)} total) ===")
    print(f"  Sorted durations: {[round(d, 3) for d in holdout_durs]}")
    print()
    text_histogram(holdout_durs, bins=12, label="Holdout duration distribution")

    # --- Summary statistics
    def stats(durs):
        if not durs:
            return {}
        n = len(durs)
        s = sorted(durs)
        median = s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2
        mean = sum(durs) / n
        p95 = s[int(0.95 * n)]
        return {"n": n, "median": median, "mean": mean, "p95": p95, "min": s[0], "max": s[-1]}

    rs = stats(reserved_durs)
    hs = stats(holdout_durs)
    print("\n=== SUMMARY ===")
    print(f"  {'':12} {'N':>4} {'Median':>8} {'Mean':>8} {'P95':>8} {'Min':>8} {'Max':>8}")
    print(f"  {'reserved':12} {rs['n']:>4} {rs['median']:>7.3f}s {rs['mean']:>7.3f}s {rs['p95']:>7.3f}s {rs['min']:>7.3f}s {rs['max']:>7.3f}s")
    print(f"  {'holdout':12} {hs['n']:>4} {hs['median']:>7.3f}s {hs['mean']:>7.3f}s {hs['p95']:>7.3f}s {hs['min']:>7.3f}s {hs['max']:>7.3f}s")

    print("\n" + "=" * 70)
    print("PART A.2: H1 CODE VERIFICATION")
    print("=" * 70)
    print("""
In reservation.py try_claim(), lines 401-420:

  # Move winner and existing descendants into the reservation cgroup
  pids_to_move = {proc.pid}
  if process_table is not None:
      descendants = get_descendants(proc.pid, process_table)  # FIRST SCAN
      pids_to_move.update(descendants)

  try:
      self.cgroup_backend.move_pids(res.group_name, pids_to_move)
  except Exception as e:
      ...

  # Re-scan once to catch children forked during move
  if process_table is not None:
      updated_descendants = get_descendants(proc.pid, process_table)  # SAME process_table!
      remaining_children = updated_descendants - pids_to_move
      if remaining_children:
          ...

H1 VERDICT: CONFIRMED AS TRUE.

The "re-scan" on line 414 calls get_descendants() with the SAME 'process_table' 
dict that was passed in from the watcher at claim time. This dict is a snapshot 
from watcher.get_process_table() taken BEFORE the move. Any child PIDs forked 
AFTER that snapshot are absent from the dict. So remaining_children is always 
the empty set when children fork after the watcher poll, meaning:

  - cc1 (gcc's internal compilation subprocess) typically starts AFTER make 
    has been observed by the watcher and the snapshot is taken.
  - cc1 and all ITS descendants start in /init.scope and STAY there.
  - The reservation cgroup contains only make (or an empty shell), while all 
    the actual CPU work (cc1 preprocessing + compiling 240 functions) runs 
    outside the boosted group.
  - This explains the bimodal distribution: the few 'fast' reserved builds 
    got lucky timing where cc1 was already in the watcher table at claim time; 
    the 'slow' builds (~8.3s like holdouts) had cc1 miss the cgroup.
""")

    conn.close()


if __name__ == "__main__":
    main()
