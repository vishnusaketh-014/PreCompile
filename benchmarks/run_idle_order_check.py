#!/usr/bin/env python3
"""Idle Order Check (Step 11b Part A.4).

Re-runs the idle sanity check (C0, C1, C3, 15 builds each, no background load)
with RANDOMISED block order (seeded with 42).
Reports block order, per-block medians, Mann-Whitney U p-value, and bootstrap CI
for C0 vs C1 and C0 vs C3.
If the ~4% advantage of C1 over C0 persists, runs a 30-build alternating C0 vs C1 test.
"""

import math
import random
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = Path("/home/saketh/precompile-demo-heavy")
OUTPUT_DIR = SCRIPT_DIR / "results_idle_order"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def start_daemon(condition: str, db_path: Path, pid_file: Path, log_file: Path) -> Optional[subprocess.Popen]:
    if condition == "c0-baseline":
        return None

    cmd = [
        sys.executable, "-m", "precompile.cli", "daemon",
        "--actuate",
        "--weight", "500",
        "--db", str(db_path),
        "--pid-file", str(pid_file),
        "--log-level", "INFO",
        "--leak-check",
    ]
    if condition == "c1-reactive":
        cmd.extend(["--condition", "c1-reactive"])
    elif condition == "c3-static":
        cmd.extend(["--condition", "c3-static"])

    log_fp = open(log_file, "w")
    proc = subprocess.Popen(cmd, stdout=log_fp, stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if pid_file.exists():
            break
        time.sleep(0.05)
    return proc


def stop_daemon(proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)


def mann_whitney_u(sample1: List[float], sample2: List[float]) -> Tuple[float, float]:
    """Computes Mann-Whitney U test between sample1 and sample2. Returns (U, p_value)."""
    n1 = len(sample1)
    n2 = len(sample2)
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0

    ranked = sorted([(v, 1, i) for i, v in enumerate(sample1)] + [(v, 2, i) for i, v in enumerate(sample2)], key=lambda x: x[0])
    ranks = [0.0] * len(ranked)
    i = 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j

    r1 = sum(ranks[k] for k in range(len(ranked)) if ranked[k][1] == 1)
    u1 = r1 - (n1 * (n1 + 1)) / 2.0
    u2 = (n1 * n2) - u1
    u = min(u1, u2)

    mean_u = (n1 * n2) / 2.0
    var_u = (n1 * n2 * (n1 + n2 + 1)) / 12.0
    if var_u <= 0:
        return u, 1.0
    z = (u - mean_u) / math.sqrt(var_u)
    # Two-sided p-value using standard normal approx
    p = 2.0 * 0.5 * math.erfc(abs(z) / math.sqrt(2))
    return u, p


def bootstrap_ci(s_eval: List[float], s_base: List[float], n_boot: int = 2000, alpha: float = 0.05) -> Tuple[float, float, float]:
    """Relative effect: (median(base) - median(eval)) / median(base)."""
    from precompile.stats import median
    m_base = median(s_base)
    m_eval = median(s_eval)
    observed_effect = (m_base - m_eval) / m_base if m_base > 0 else 0.0

    rng = random.Random(1337)
    effects = []
    for _ in range(n_boot):
        b_sample = [s_base[rng.randint(0, len(s_base) - 1)] for _ in range(len(s_base))]
        e_sample = [s_eval[rng.randint(0, len(s_eval) - 1)] for _ in range(len(s_eval))]
        mb = median(b_sample)
        me = median(e_sample)
        eff = (mb - me) / mb if mb > 0 else 0.0
        effects.append(eff)

    effects.sort()
    lo_idx = int((alpha / 2.0) * n_boot)
    hi_idx = int((1.0 - alpha / 2.0) * n_boot)
    return observed_effect, effects[lo_idx], effects[hi_idx]


def run_randomized_blocks() -> Tuple[List[Dict[str, Any]], Dict[str, List[float]]]:
    print("=" * 80)
    print("PreCompile Step 11b Part A.4: Idle Order Check with RANDOMISED Blocks")
    print("15 builds each of C0, C1, and C3 (9 blocks of 5 builds) at idle")
    print("=" * 80)

    # 1. Generate and randomize sequence with seed 42
    blocks = [
        (1, "c0-baseline"), (2, "c0-baseline"), (3, "c0-baseline"),
        (1, "c1-reactive"), (2, "c1-reactive"), (3, "c1-reactive"),
        (1, "c3-static"),   (2, "c3-static"),   (3, "c3-static"),
    ]
    rng = random.Random(42)
    rng.shuffle(blocks)

    print("\n[RANDOMIZED BLOCK ORDER (Seed 42)]:")
    for idx, (cy, cn) in enumerate(blocks, 1):
        print(f"  Block {idx}/9: {cn} (cycle {cy})")

    # Stop any background load and cleanup
    subprocess.run([str(SCRIPT_DIR / "load.sh"), "stop"], check=False)
    subprocess.run([sys.executable, "-m", "precompile.cli", "cleanup"], check=False)

    all_records: List[Dict[str, Any]] = []
    durations_by_cond: Dict[str, List[float]] = {"c0-baseline": [], "c1-reactive": [], "c3-static": []}
    block_medians: List[Dict[str, Any]] = []

    from precompile.stats import median

    for b_idx, (cy, cn) in enumerate(blocks, 1):
        print(f"\n>>> Running Block {b_idx}/9: Condition={cn}, Cycle={cy} (5 builds)")
        db_path = OUTPUT_DIR / f"db_{cn}.db"
        pid_file = OUTPUT_DIR / f"pid_{cn}_b{b_idx}.pid"
        log_file = OUTPUT_DIR / f"log_{cn}_b{b_idx}.log"

        if cy == 1 and not db_path.exists():
            run_cmd([
                sys.executable, "-m", "precompile.cli", "projects", "add",
                "--id", "heavy",
                "--path", str(PROJECT_DIR),
                "--tools", "gcc,make",
                "--db", str(db_path),
            ], check=True)

        daemon_proc = start_daemon(cn, db_path, pid_file, log_file)
        time.sleep(1.0)

        block_durs = []
        try:
            for i in range(5):
                sim_cmd = [
                    sys.executable, str(SCRIPT_DIR / "build_on_save.py"),
                    "--project", str(PROJECT_DIR),
                    "--cycles", "1",
                    "--edit-file", "big.c",
                    "--delay-mean", "1.8",
                    "--delay-jitter", "0.1",
                    "--mode", "auto",
                    "--cpuset", "0-1",
                ]
                t0 = time.monotonic()
                res = subprocess.run(sim_cmd, timeout=60.0, capture_output=True, text=True)
                dur_fallback = time.monotonic() - t0

                m_bwall = re.search(r"build_wall_s=([\d\.]+)", res.stdout)
                build_wall_s = float(m_bwall.group(1)) if m_bwall else dur_fallback

                record = {
                    "condition": cn,
                    "cycle": cy,
                    "block": b_idx,
                    "build_in_block": i + 1,
                    "build_wall_s": build_wall_s,
                }
                all_records.append(record)
                durations_by_cond[cn].append(build_wall_s)
                block_durs.append(build_wall_s)
                print(f"  Build {i+1}/5: build_wall_s={build_wall_s:.4f}s")
        finally:
            stop_daemon(daemon_proc)

        b_med = median(block_durs)
        block_medians.append({"block": b_idx, "condition": cn, "cycle": cy, "median_s": b_med})
        print(f"  -> Block {b_idx} ({cn}) Median: {b_med:.4f}s")

    # Clean up
    subprocess.run([sys.executable, "-m", "precompile.cli", "cleanup"], check=False)
    return block_medians, durations_by_cond


def run_alternating_c0_c1_check() -> Tuple[List[float], List[float]]:
    print("\n" + "=" * 80)
    print("PreCompile Step 11b Part A.4: Alternating C0 vs C1 Test (30 builds total)")
    print("Alternating build-by-build: C0, C1, C0, C1, ... (15 builds each at idle)")
    print("=" * 80)

    db_path = OUTPUT_DIR / "db_c1_alt.db"
    if db_path.exists():
        db_path.unlink()

    run_cmd([
        sys.executable, "-m", "precompile.cli", "projects", "add",
        "--id", "heavy",
        "--path", str(PROJECT_DIR),
        "--tools", "gcc,make",
        "--db", str(db_path),
    ], check=True)

    c0_alt: List[float] = []
    c1_alt: List[float] = []

    for i in range(15):
        pair_num = i + 1
        print(f"\n--- Pair {pair_num}/15 ---")

        # 1. C0 build
        sim_cmd = [
            sys.executable, str(SCRIPT_DIR / "build_on_save.py"),
            "--project", str(PROJECT_DIR),
            "--cycles", "1",
            "--edit-file", "big.c",
            "--delay-mean", "1.8",
            "--delay-jitter", "0.1",
            "--mode", "auto",
            "--cpuset", "0-1",
        ]
        t0 = time.monotonic()
        res = subprocess.run(sim_cmd, timeout=60.0, capture_output=True, text=True)
        dur_fallback = time.monotonic() - t0
        m_bwall = re.search(r"build_wall_s=([\d\.]+)", res.stdout)
        b_c0 = float(m_bwall.group(1)) if m_bwall else dur_fallback
        c0_alt.append(b_c0)
        print(f"  [C0 #{pair_num}] build_wall_s={b_c0:.4f}s")

        # 2. C1 build (with daemon running for this single build)
        pid_file = OUTPUT_DIR / f"pid_c1_alt_{pair_num}.pid"
        log_file = OUTPUT_DIR / f"log_c1_alt_{pair_num}.log"
        daemon_proc = start_daemon("c1-reactive", db_path, pid_file, log_file)
        time.sleep(0.5)
        try:
            t0 = time.monotonic()
            res = subprocess.run(sim_cmd, timeout=60.0, capture_output=True, text=True)
            dur_fallback = time.monotonic() - t0
            m_bwall = re.search(r"build_wall_s=([\d\.]+)", res.stdout)
            b_c1 = float(m_bwall.group(1)) if m_bwall else dur_fallback
            c1_alt.append(b_c1)
            print(f"  [C1 #{pair_num}] build_wall_s={b_c1:.4f}s")
        finally:
            stop_daemon(daemon_proc)

    # Clean up
    subprocess.run([sys.executable, "-m", "precompile.cli", "cleanup"], check=False)
    return c0_alt, c1_alt


def main():
    block_medians, durations = run_randomized_blocks()

    from precompile.stats import median
    c0 = durations["c0-baseline"]
    c1 = durations["c1-reactive"]
    c3 = durations["c3-static"]

    def mean_std(data):
        m = sum(data) / len(data)
        v = sum((x - m)**2 for x in data) / (len(data) - 1) if len(data) > 1 else 0.0
        return m, v**0.5

    med_c0 = median(c0)
    med_c1 = median(c1)
    med_c3 = median(c3)

    m_c0, s_c0 = mean_std(c0)
    m_c1, s_c1 = mean_std(c1)
    m_c3, s_c3 = mean_std(c3)

    diff_c1 = (med_c0 - med_c1) / med_c0 * 100.0
    diff_c3 = (med_c0 - med_c3) / med_c0 * 100.0

    _, p_c1 = mann_whitney_u(c1, c0)
    _, p_c3 = mann_whitney_u(c3, c0)

    eff_c1, ci_c1_lo, ci_c1_hi = bootstrap_ci(c1, c0)
    eff_c3, ci_c3_lo, ci_c3_hi = bootstrap_ci(c3, c0)

    print("\n" + "=" * 110)
    print("PER-BLOCK MEDIANS (RANDOMIZED ORDER):")
    print("=" * 110)
    print(f"| {'Block':<6} | {'Condition':<14} | {'Cycle':<6} | {'Median Wall (s)':<16} |")
    print(f"|{'-'*8}|{'-'*16}|{'-'*8}|{'-'*18}|")
    for bm in block_medians:
        print(f"| {bm['block']:<6} | {bm['condition']:<14} | {bm['cycle']:<6} | {bm['median_s']:.4f}s           |")
    print("=" * 110)

    print("\n" + "=" * 110)
    print("OVERALL IDLE SANITY STATISTICAL SUMMARY (15 builds per condition, randomized):")
    print("=" * 110)
    print(f"| {'Comparison':<16} | {'Base Med':<10} | {'Eval Med':<10} | {'Speedup (%)':<12} | {'95% Bootstrap CI':<22} | {'p-value':<10} |")
    print(f"|{'-'*18}|{'-'*12}|{'-'*12}|{'-'*14}|{'-'*24}|{'-'*12}|")
    print(f"| {'C0 vs C1':<16} | {med_c0:.4f}s    | {med_c1:.4f}s    | {eff_c1*100:+.2f}%      | [{ci_c1_lo*100:+.1f}%, {ci_c1_hi*100:+.1f}%]         | {p_c1:.4f}     |")
    print(f"| {'C0 vs C3':<16} | {med_c0:.4f}s    | {med_c3:.4f}s    | {eff_c3*100:+.2f}%      | [{ci_c3_lo*100:+.1f}%, {ci_c3_hi*100:+.1f}%]         | {p_c3:.4f}     |")
    print("=" * 110)

    print(f"\nCondition Medians & Means:")
    print(f"  C0: Median={med_c0:.4f}s, Mean={m_c0:.4f}s ± {s_c0:.4f}s")
    print(f"  C1: Median={med_c1:.4f}s, Mean={m_c1:.4f}s ± {s_c1:.4f}s (Speedup: {eff_c1*100:+.2f}%)")
    print(f"  C3: Median={med_c3:.4f}s, Mean={m_c3:.4f}s ± {s_c3:.4f}s (Speedup: {eff_c3*100:+.2f}%)")

    persists = (eff_c1 > 0.02)
    print(f"\nDid earlier ~4% advantage of daemon condition persist under randomized block order? {persists} (Speedup={eff_c1*100:+.2f}%, p={p_c1:.4f})")

    if persists:
        print("\n--> C1 advantage persists (> 2.0%). Executing 30-build alternating C0 vs C1 test to separate block effects from real effect...")
        c0_alt, c1_alt = run_alternating_c0_c1_check()
        med_c0_alt = median(c0_alt)
        med_c1_alt = median(c1_alt)
        m_c0_alt, s_c0_alt = mean_std(c0_alt)
        m_c1_alt, s_c1_alt = mean_std(c1_alt)
        _, p_alt = mann_whitney_u(c1_alt, c0_alt)
        eff_alt, ci_alt_lo, ci_alt_hi = bootstrap_ci(c1_alt, c0_alt)

        print("\n" + "=" * 110)
        print("ALTERNATING C0 VS C1 SUMMARY (15 pairs = 30 builds):")
        print("=" * 110)
        print(f"| {'Condition':<14} | {'N':<4} | {'Median Wall':<12} | {'Mean ± Std':<16} | {'Speedup (%)':<14} | {'95% Bootstrap CI':<20} | {'p-value':<10} |")
        print(f"|{'-'*16}|{'-'*6}|{'-'*14}|{'-'*18}|{'-'*16}|{'-'*22}|{'-'*12}|")
        print(f"| {'C0 Baseline':<14} | {len(c0_alt):<4} | {med_c0_alt:.4f}s      | {m_c0_alt:.4f} ± {s_c0_alt:.4f}s | {'Reference':<14} | {'N/A':<20} | {'N/A':<10} |")
        print(f"| {'C1 Reactive':<14} | {len(c1_alt):<4} | {med_c1_alt:.4f}s      | {m_c1_alt:.4f} ± {s_c1_alt:.4f}s | {eff_alt*100:+.2f}%        | [{ci_alt_lo*100:+.1f}%, {ci_alt_hi*100:+.1f}%]       | {p_alt:.4f}     |")
        print("=" * 110)
    else:
        print("\n--> Advantage eliminated by block randomization (order effect). No alternating run needed.")


if __name__ == "__main__":
    main()
