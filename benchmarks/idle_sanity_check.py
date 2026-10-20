#!/usr/bin/env python3
"""Idle sanity check: 15 builds each of C0, C1, and C3 interleaved in blocks of 5 at idle.

Verifies that the canonical build_wall_s measurement agrees within +/- 5% across C0, C1, and C3.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = Path("/home/saketh/precompile-demo-heavy")
OUTPUT_DIR = SCRIPT_DIR / "results_idle_sanity"
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


def main():
    print("=" * 80)
    print("PreCompile Step 11a4: Idle Measurement Sanity Check (15 builds each C0, C1, C3)")
    print("Interleaved in blocks of 5 at idle (no background load)")
    print("=" * 80)

    # Ensure no background load or cgroups
    subprocess.run([str(SCRIPT_DIR / "load.sh"), "stop"], check=False)
    subprocess.run([sys.executable, "-m", "precompile.cli", "cleanup"], check=False)

    sequence = [
        # (cycle, condition)
        (1, "c0-baseline"), (1, "c1-reactive"), (1, "c3-static"),
        (2, "c0-baseline"), (2, "c1-reactive"), (2, "c3-static"),
        (3, "c0-baseline"), (3, "c1-reactive"), (3, "c3-static"),
    ]

    all_records: List[Dict[str, Any]] = []
    wall_diffs: List[float] = []

    for b_idx, (cy, cn) in enumerate(sequence, 1):
        print(f"\n>>> Running Block {b_idx}/9: Condition={cn}, Cycle={cy} (5 builds)")
        db_path = OUTPUT_DIR / f"db_{cn}.db"
        pid_file = OUTPUT_DIR / f"pid_{cn}_c{cy}.pid"
        log_file = OUTPUT_DIR / f"log_{cn}_c{cy}.log"

        if cy == 1 and db_path.exists():
            db_path.unlink()

        # Clean and pre-compile project
        run_cmd(["make", "-C", str(PROJECT_DIR), "clean"], check=False)
        run_cmd(["make", "-C", str(PROJECT_DIR)], check=True)

        if cy == 1 or not db_path.exists():
            run_cmd([
                sys.executable, "-m", "precompile.cli", "projects", "add",
                "--id", "heavy",
                "--path", str(PROJECT_DIR),
                "--tools", "gcc,make",
                "--db", str(db_path),
            ], check=True)

        daemon_proc = start_daemon(cn, db_path, pid_file, log_file)
        time.sleep(1.0)

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

                # Check daemon duration if DB exists
                daemon_dur = None
                if db_path.exists() and cn != "c0-baseline":
                    import sqlite3
                    with sqlite3.connect(db_path) as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT duration_s FROM builds ORDER BY id DESC LIMIT 1")
                        row = cur.fetchone()
                        if row:
                            daemon_dur = row[0]

                diff = abs(build_wall_s - daemon_dur) if daemon_dur is not None else None
                if diff is not None:
                    wall_diffs.append(diff)

                record = {
                    "condition": cn,
                    "cycle": cy,
                    "block": b_idx,
                    "build_in_block": i + 1,
                    "build_wall_s": build_wall_s,
                    "daemon_duration_s": daemon_dur,
                    "wall_daemon_diff_s": diff,
                }
                all_records.append(record)
                diff_str = f" | diff={diff*1000:.2f}ms" if diff is not None else ""
                print(f"  Build {i+1}/5: build_wall_s={build_wall_s:.4f}s{diff_str}")
        finally:
            stop_daemon(daemon_proc)

    # Clean up
    subprocess.run([sys.executable, "-m", "precompile.cli", "cleanup"], check=False)

    # Compute summary statistics per condition
    from precompile.stats import median, percentile
    c0_durs = [r["build_wall_s"] for r in all_records if r["condition"] == "c0-baseline"]
    c1_durs = [r["build_wall_s"] for r in all_records if r["condition"] == "c1-reactive"]
    c3_durs = [r["build_wall_s"] for r in all_records if r["condition"] == "c3-static"]

    med_c0 = median(c0_durs)
    med_c1 = median(c1_durs)
    med_c3 = median(c3_durs)

    diff_c1 = (med_c1 - med_c0) / med_c0 * 100.0
    diff_c3 = (med_c3 - med_c0) / med_c0 * 100.0

    agrees_5pct = (abs(diff_c1) <= 5.0) and (abs(diff_c3) <= 5.0)

    print("\n" + "=" * 90)
    print("IDLE MEASUREMENT SANITY CHECK SUMMARY (15 builds per condition)")
    print("=" * 90)
    print(f"| {'Condition':<14} | {'N':<4} | {'Median Wall':<12} | {'Mean ± Std':<16} | {'Delta vs C0 (%)':<18} | {'Status':<10} |")
    print(f"|{'-'*16}|{'-'*6}|{'-'*14}|{'-'*18}|{'-'*20}|{'-'*12}|")

    def mean_std(data):
        m = sum(data) / len(data)
        v = sum((x - m)**2 for x in data) / (len(data) - 1) if len(data) > 1 else 0.0
        return m, v**0.5

    m_c0, s_c0 = mean_std(c0_durs)
    m_c1, s_c1 = mean_std(c1_durs)
    m_c3, s_c3 = mean_std(c3_durs)

    print(f"| {'c0-baseline':<14} | {len(c0_durs):<4} | {med_c0:.4f}s      | {m_c0:.4f} ± {s_c0:.4f}s | {'0.00% (reference)':<18} | {'PASS':<10} |")
    print(f"| {'c1-reactive':<14} | {len(c1_durs):<4} | {med_c1:.4f}s      | {m_c1:.4f} ± {s_c1:.4f}s | {diff_c1:+.2f}%{'':<11} | {'PASS' if abs(diff_c1) <= 5.0 else 'FAIL':<10} |")
    print(f"| {'c3-static':<14} | {len(c3_durs):<4} | {med_c3:.4f}s      | {m_c3:.4f} ± {s_c3:.4f}s | {diff_c3:+.2f}%{'':<11} | {'PASS' if abs(diff_c3) <= 5.0 else 'FAIL':<10} |")
    print("=" * 90)
    print(f"Agreement within ±5.0%: {'PASSED' if agrees_5pct else 'FAILED'}")

    if wall_diffs:
        print("\n" + "=" * 90)
        print("CROSS-CHECK: |build_wall_s - daemon_duration_s| DISTRIBUTION (Boosted conditions)")
        print("=" * 90)
        print(f"Sample size: {len(wall_diffs)} builds")
        print(f"Min:    {min(wall_diffs)*1000:.2f} ms")
        print(f"p25:    {percentile(wall_diffs, 25)*1000:.2f} ms")
        print(f"Median: {median(wall_diffs)*1000:.2f} ms")
        print(f"p75:    {percentile(wall_diffs, 75)*1000:.2f} ms")
        print(f"Max:    {max(wall_diffs)*1000:.2f} ms")
        print(f"Mean:   {sum(wall_diffs)/len(wall_diffs)*1000:.2f} ms")
        print("=" * 90)

    # Save results to json
    results_summary = {
        "agrees_5pct": agrees_5pct,
        "c0": {"median": med_c0, "mean": m_c0, "std": s_c0},
        "c1": {"median": med_c1, "mean": m_c1, "std": s_c1, "delta_pct": diff_c1},
        "c3": {"median": med_c3, "mean": m_c3, "std": s_c3, "delta_pct": diff_c3},
        "diff_distribution_ms": {
            "min": min(wall_diffs)*1000 if wall_diffs else 0.0,
            "median": median(wall_diffs)*1000 if wall_diffs else 0.0,
            "p75": percentile(wall_diffs, 75)*1000 if wall_diffs else 0.0,
            "max": max(wall_diffs)*1000 if wall_diffs else 0.0,
            "mean": (sum(wall_diffs)/len(wall_diffs)*1000) if wall_diffs else 0.0,
        }
    }
    with open(OUTPUT_DIR / "idle_sanity_summary.json", "w", encoding="utf-8") as f:
        json.dump(results_summary, f, indent=2)


if __name__ == "__main__":
    main()
