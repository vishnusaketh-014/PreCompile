#!/usr/bin/env python3
"""Build Drift Check over 60 Idle Builds.

Stage Mapping: Step 11a2 Item 7.
Runs 60 consecutive idle builds using non-growing edits on big.c.
Compares first 30 builds (start) vs last 30 builds (end):
- Computes median and 95% bootstrap CI for first 30.
- Computes median and 95% bootstrap CI for last 30.
- Assesses whether compilation duration exhibits systematic drift.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from precompile.stats import bootstrap_relative_effect_ci, median

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = Path("/home/saketh/precompile-demo-heavy")
CPUSET = "0-1"
TOTAL_BUILDS = 60


def main():
    print(f"Starting Drift Check: {TOTAL_BUILDS} idle builds on {PROJECT_DIR} (CPU set {CPUSET})...")

    # Ensure load is stopped
    subprocess.run([str(SCRIPT_DIR / "load.sh"), "stop"], check=False)
    time.sleep(2)

    # Pre-clean
    subprocess.run(["make", "-C", str(PROJECT_DIR), "clean"], check=False, stdout=subprocess.DEVNULL)
    subprocess.run(["make", "-C", str(PROJECT_DIR)], check=True, stdout=subprocess.DEVNULL)

    durations = []

    for i in range(1, TOTAL_BUILDS + 1):
        sim_cmd = [
            sys.executable, str(SCRIPT_DIR / "build_on_save.py"),
            "--project", str(PROJECT_DIR),
            "--cycles", "1",
            "--edit-file", "big.c",
            "--delay-mean", "0.5",
            "--delay-jitter", "0.01",
            "--mode", "auto",
            "--cpuset", CPUSET,
        ]
        t0 = time.monotonic()
        subprocess.run(sim_cmd, timeout=30, check=True, stdout=subprocess.DEVNULL)
        dur = time.monotonic() - t0
        durations.append(dur)
        if i % 10 == 0 or i == TOTAL_BUILDS:
            print(f"  Completed {i:>2}/{TOTAL_BUILDS} builds... (latest={dur:.3f}s)")

    first_half = durations[:30]
    second_half = durations[30:]

    med_first = median(first_half)
    med_second = median(second_half)

    # Relative difference: (med_second - med_first) / med_first
    diff_pct = (med_second - med_first) / med_first * 100.0

    # 95% bootstrap CIs
    _, ci_first_lo, ci_first_hi = bootstrap_relative_effect_ci({"all": first_half}, {"all": first_half}, n_resamples=1000)
    # Direct bootstrap CI on median of each half
    import random
    def bootstrap_median_ci(data: list, n_resamples: int = 1000):
        rng = random.Random(42)
        n = len(data)
        boots = []
        for _ in range(n_resamples):
            resamp = [data[rng.randrange(n)] for _ in range(n)]
            boots.append(median(resamp))
        boots.sort()
        lo = boots[int(0.025 * n_resamples)]
        hi = boots[int(0.975 * n_resamples)]
        return lo, hi

    lo_first, hi_first = bootstrap_median_ci(first_half)
    lo_second, hi_second = bootstrap_median_ci(second_half)

    print("\n" + "=" * 80)
    print("DRIFT CHECK RESULTS (60 IDLE BUILDS)")
    print("=" * 80)
    print(f"First 30 Builds  (Start): Median = {med_first:.3f}s (95% CI: [{lo_first:.3f}s, {hi_first:.3f}s])")
    print(f"Second 30 Builds (End):   Median = {med_second:.3f}s (95% CI: [{lo_second:.3f}s, {hi_second:.3f}s])")
    print(f"Median Delta:             {diff_pct:+.2f}%")
    drift_detected = abs(diff_pct) > 5.0
    print(f"Drift Conclusion:         {'SIGNIFICANT DRIFT (>5%)' if drift_detected else 'STABLE (NO DRIFT)'}")
    print("=" * 80)


if __name__ == "__main__":
    main()
