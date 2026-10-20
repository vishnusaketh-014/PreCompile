#!/usr/bin/env python3
"""Claim Latency Microbenchmark.

Stage Mapping: Step 11a2 Item 3.
Measures 40 claims at high CPU load on the heavy project for each condition:
- c1-reactive
- c2-full (warm, seeded model)
- c3-static

Starts the daemon ONCE per condition.
Decomposes total claim latency into:
1. Detection Latency: t_seen - t_start (kernel spawn to /proc scan detection)
2. Dispatch Latency: t_claim_start - t_seen (queue/matcher delivery to claim attempt)
3. Execution Latency: t_claim_end - t_claim_start (cgroup migration / sysfs write)
4. Total Claim Latency: t_claim_end - t_start

Also records the watcher mode (normal vs intensified) active at the instant of process detection.
"""

import argparse
import json
import math
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
DEFAULT_PROJECT_DIR = Path("/home/saketh/precompile-demo-heavy")
DEFAULT_CPUSET = "0-1"
CLAIMS_TARGET = 40


def calc_stats(values: List[float]) -> Tuple[float, float, float, float]:
    """Return (mean, std, median, p95) for a list of values."""
    if not values:
        return 0.0, 0.0, 0.0, 0.0
    s_vals = sorted(values)
    n = len(s_vals)
    mean_val = sum(s_vals) / n
    var = sum((x - mean_val) ** 2 for x in s_vals) / (n - 1) if n > 1 else 0.0
    std_val = math.sqrt(max(0.0, var))

    mid = n // 2
    med_val = (s_vals[mid - 1] + s_vals[mid]) / 2.0 if n % 2 == 0 else s_vals[mid]

    idx95 = int(math.ceil(0.95 * n)) - 1
    idx95 = max(0, min(n - 1, idx95))
    p95_val = s_vals[idx95]

    return mean_val, std_val, med_val, p95_val


def run_condition_microbench(
    condition: str,
    project_dir: Path,
    cpuset: str,
    target_claims: int = CLAIMS_TARGET,
) -> Dict[str, Any]:
    print(f"\n================================================================================")
    print(f"Starting Latency Microbenchmark for: {condition} ({target_claims} claims)")
    print(f"================================================================================")

    db_path = Path(f"/tmp/microbench_{condition}.db")
    pid_file = Path(f"/tmp/microbench_{condition}.pid")
    log_file = Path(f"/tmp/microbench_{condition}.log")

    for f in [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm"), pid_file, log_file]:
        if f.exists():
            f.unlink()

    # Pre-build initial artifacts
    subprocess.run(["make", "-C", str(project_dir), "clean"], check=False, stdout=subprocess.DEVNULL)
    subprocess.run(["make", "-C", str(project_dir)], check=True, stdout=subprocess.DEVNULL)

    # Register project
    subprocess.run([
        sys.executable, "-m", "precompile.cli", "projects", "add",
        "--id", "heavy",
        "--path", str(project_dir),
        "--tools", "gcc,make",
        "--db", str(db_path),
    ], check=True)

    # For c2-full, warm up the model to steady-state
    if condition == "c2-full":
        print("Pre-seeding steady-state / warm model for c2-full...")
        from precompile.store import DataStore
        with DataStore(db_path=db_path) as store:
            for i in range(6):
                t_s = time.time() - (6 - i) * 10
                store.record_sample("heavy", "source", "gcc,make", 1.8, accepted=True, t_save_wall=t_s)
                pid = store.record_prediction("heavy", "source", "gcc,make", t_s, 1.4, 2.2, armed=True, holdout=False)
                store.score_prediction(pid, outcome="hit", observed_delay_s=1.8)
                store.record_build("heavy", "observed", 0.0, 0.5, t_s, t_s + 0.5, prediction_id=pid)
            store.save_model_state(("heavy", "source", "gcc,make"), 6, 1.80, 0.01)

    # Start high CPU contention load
    print(f"Starting high load pinned to cpuset {cpuset}...")
    load_script = SCRIPT_DIR / "load.sh"
    env = os.environ.copy()
    env["CPUSET"] = cpuset
    subprocess.run([str(load_script), "high"], env=env, check=True)
    time.sleep(12)  # Wait for PSI avg10 to establish

    # Start PreCompile daemon
    daemon_cmd = [
        sys.executable, "-m", "precompile.cli", "daemon",
        "--condition", condition,
        "--actuate",
        "--weight", "500",
        "--db", str(db_path),
        "--pid-file", str(pid_file),
        "--log-level", "INFO",
    ]
    if condition == "c2-full":
        daemon_cmd.extend(["--holdout-prob", "0.0"])  # 0 holdouts to capture 40 consecutive claims

    log_fp = open(log_file, "w")
    daemon_proc = subprocess.Popen(daemon_cmd, stdout=log_fp, stderr=subprocess.STDOUT, text=True)

    # Wait for daemon PID file
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if pid_file.exists():
            break
        time.sleep(0.05)

    samples: List[Dict[str, Any]] = []

    try:
        print(f"Executing save-and-build iterations until {target_claims} claims collected...")
        cycle = 0
        while len(samples) < target_claims:
            cycle += 1
            sim_cmd = [
                sys.executable, str(SCRIPT_DIR / "build_on_save.py"),
                "--project", str(project_dir),
                "--cycles", "1",
                "--edit-file", "big.c",
                "--delay-mean", "1.8",
                "--delay-jitter", "0.05",
                "--mode", "auto",
                "--cpuset", cpuset,
            ]
            t0 = time.monotonic()
            sim_res = subprocess.run(sim_cmd, timeout=60, capture_output=True, text=True)
            dur = time.monotonic() - t0

            # Query database for claimed reservation
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT * FROM reservations WHERE claimed_pid IS NOT NULL ORDER BY rowid DESC LIMIT 1")
                r_row = cur.fetchone()

            if r_row is not None:
                r_dict = dict(r_row)
                res_id = r_dict["id"]
                # Only record if we haven't already processed this reservation id
                if not any(s["res_id"] == res_id for s in samples):
                    claim_lat_ms = (r_dict["claim_latency_s"] * 1000.0) if r_dict.get("claim_latency_s") is not None else None
                    samples.append({
                        "res_id": res_id,
                        "cycle": cycle,
                        "claim_latency_ms": claim_lat_ms,
                        "claimed_pid": r_dict.get("claimed_pid"),
                        "group_name": r_dict.get("group_name"),
                    })
                    print(f"  Claim {len(samples):>2}/{target_claims}: res={res_id} lat={claim_lat_ms:.1f}ms")

    finally:
        # Stop daemon
        if daemon_proc.poll() is None:
            daemon_proc.send_signal(signal.SIGTERM)
            try:
                daemon_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                daemon_proc.kill()
                daemon_proc.wait(timeout=2.0)
        log_fp.close()

        # Stop load
        subprocess.run([str(load_script), "stop"], check=False)
        time.sleep(2)
        subprocess.run([sys.executable, "-m", "precompile.cli", "cleanup"], check=False)

    # Parse log file for detailed component breakdowns and watcher mode at detection
    import re
    log_content = log_file.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        r"\[claim_breakdown\] res=(\S+) pid=(\d+) det_lat=([\d\.]+)ms disp_lat=([\d\.]+)ms exec_lat=([\d\.]+)ms total_lat=([\d\.]+)ms mode=(\S+)"
    )

    det_lats: List[float] = []
    disp_lats: List[float] = []
    exec_lats: List[float] = []
    tot_lats: List[float] = []
    modes: List[str] = []

    for m in pattern.finditer(log_content):
        det_lats.append(float(m.group(3)))
        disp_lats.append(float(m.group(4)))
        exec_lats.append(float(m.group(5)))
        tot_lats.append(float(m.group(6)))
        modes.append(m.group(7))

    # Fallback to DB latencies if breakdown regex not found
    if not tot_lats:
        tot_lats = [s["claim_latency_ms"] for s in samples if s["claim_latency_ms"] is not None]

    mean_tot, std_tot, med_tot, p95_tot = calc_stats(tot_lats)
    mean_det, std_det, med_det, p95_det = calc_stats(det_lats)
    mean_disp, std_disp, med_disp, p95_disp = calc_stats(disp_lats)
    mean_exec, std_exec, med_exec, p95_exec = calc_stats(exec_lats)

    normal_cnt = sum(1 for m in modes if m == "normal")
    intensified_cnt = sum(1 for m in modes if m == "intensified")

    print(f"\nCompleted {condition}: N={len(tot_lats)}")
    print(f"  Total Latency:    Mean={mean_tot:.2f}ms, Med={med_tot:.2f}ms, P95={p95_tot:.2f}ms")
    print(f"  Detection (t_seen-t_start): Mean={mean_det:.2f}ms, Med={med_det:.2f}ms, P95={p95_det:.2f}ms")
    print(f"  Dispatch  (t_claim-t_seen): Mean={mean_disp:.2f}ms, Med={med_disp:.2f}ms, P95={p95_disp:.2f}ms")
    print(f"  Execution (cgroup move):    Mean={mean_exec:.2f}ms, Med={med_exec:.2f}ms, P95={p95_exec:.2f}ms")
    print(f"  Watcher Mode at Detection:  Normal={normal_cnt}, Intensified={intensified_cnt}")

    return {
        "condition": condition,
        "n_claims": len(tot_lats),
        "total": {"mean": mean_tot, "std": std_tot, "med": med_tot, "p95": p95_tot},
        "detection": {"mean": mean_det, "std": std_det, "med": med_det, "p95": p95_det},
        "dispatch": {"mean": mean_disp, "std": std_disp, "med": med_disp, "p95": p95_disp},
        "execution": {"mean": mean_exec, "std": std_exec, "med": med_exec, "p95": p95_exec},
        "modes": {"normal": normal_cnt, "intensified": intensified_cnt},
        "samples": samples,
    }


def main():
    if os.geteuid() != 0:
        print("Error: Latency microbenchmark requires root privileges.", file=sys.stderr)
        sys.exit(1)

    project_dir = DEFAULT_PROJECT_DIR
    if not project_dir.exists():
        print(f"Error: Project directory {project_dir} does not exist.", file=sys.stderr)
        sys.exit(1)

    conditions = ["c1-reactive", "c2-full", "c3-static"]
    results = {}

    for cond in conditions:
        res = run_condition_microbench(cond, project_dir=project_dir, cpuset=DEFAULT_CPUSET, target_claims=CLAIMS_TARGET)
        results[cond] = res

    # Output comparison table
    print("\n" + "=" * 105)
    print(f"CLAIM LATENCY MICROBENCHMARK RESULTS ({CLAIMS_TARGET} CLAIMS PER CONDITION, HIGH LOAD)")
    print("=" * 105)
    print(f"| {'Condition':<14} | {'N':<4} | {'Total Med (ms)':<15} | {'Detection Med':<15} | {'Dispatch Med':<15} | {'Execution Med':<15} | {'Mode (N/I)':<12} |")
    print(f"|{'-'*16}|{'-'*6}|{'-'*17}|{'-'*17}|{'-'*17}|{'-'*17}|{'-'*14}|")
    for cond in conditions:
        r = results[cond]
        tot = f"{r['total']['med']:.2f} (mean {r['total']['mean']:.1f})"
        det = f"{r['detection']['med']:.2f} ms"
        disp = f"{r['dispatch']['med']:.2f} ms"
        exc = f"{r['execution']['med']:.2f} ms"
        modes = f"{r['modes']['normal']}/{r['modes']['intensified']}"
        print(f"| {cond:<14} | {r['n_claims']:<4} | {tot:<15} | {det:<15} | {disp:<15} | {exc:<15} | {modes:<12} |")
    print("=" * 105)

    out_json = Path("/tmp/latency_microbench_results.json")
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nDetailed JSON saved to {out_json}")
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nDetailed JSON saved to {out_json}")


if __name__ == "__main__":
    main()
