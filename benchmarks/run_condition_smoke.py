"""Root smoke test comparing baseline conditions: c1-reactive, c2-full, and c3-static.

Runs cycles on the heavy project for each condition under both idle and high load.
Extracts and compares:
- Claims count
- Claim latency (mean and median in ms)
- Watcher scan duration (avg in ms)
- Watcher scans per second (normal, intensified, overall)
"""

import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def run_command(cmd: List[str], check: bool = True, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, timeout=timeout, capture_output=True, text=True)


def get_leftover_cgroups() -> List[str]:
    cgroup_root = Path("/sys/fs/cgroup")
    return [p.name for p in cgroup_root.glob("precompile-res-*") if p.is_dir()]


def run_condition_test(
    condition: str,
    load_level: str,
    project_dir: Path,
    cpuset: str = "0-1",
    cycles: int = 6,
) -> Dict[str, Any]:
    print(f"\n==================================================")
    print(f"Running Smoke Test: condition={condition}, load={load_level}")
    print(f"==================================================")

    db_path = Path(f"/tmp/smoke_{condition}_{load_level}.db")
    pid_file = Path(f"/tmp/smoke_{condition}_{load_level}.pid")
    log_file = Path(f"/tmp/smoke_{condition}_{load_level}.log")

    for f in [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm"), pid_file, log_file]:
        if f.exists():
            f.unlink()

    # Pre-build initial artifacts
    run_command(["make", "-C", str(project_dir), "clean"], check=False)
    run_command(["make", "-C", str(project_dir)], check=True)

    # Register project
    run_command([
        sys.executable, "-m", "precompile.cli", "projects", "add",
        "--id", "heavy",
        "--path", str(project_dir),
        "--tools", "gcc,make",
        "--db", str(db_path),
    ], check=True)

    # For c2-full, warm up the model to steady-state so confidence & cost gates pass
    if condition == "c2-full":
        print("Pre-seeding steady-state / warm model for c2-full...")
        from precompile.store import DataStore
        with DataStore(db_path=db_path) as store:
            for i in range(6):
                t_s = time.time() - (6 - i) * 10
                store.record_sample(
                    project_id="heavy",
                    file_class="source",
                    tool_profile="gcc,make",
                    delay_s=1.8,
                    accepted=True,
                    t_save_wall=t_s,
                )
                pid = store.record_prediction(
                    project_id="heavy",
                    file_class="source",
                    tool_profile="gcc,make",
                    t_save_wall=t_s,
                    window_lo=1.4,
                    window_hi=2.2,
                    armed=True,
                    holdout=False,
                )
                store.score_prediction(pid, outcome="hit", observed_delay_s=1.8)
                store.record_build(
                    project_id="heavy",
                    condition="observed",
                    contention_level=0.0,
                    duration_s=0.5,
                    t_start_wall=t_s,
                    t_end_wall=t_s + 0.5,
                    prediction_id=pid,
                )
            store.save_model_state(("heavy", "source", "gcc,make"), 6, 1.80, 0.01)

    # Configure load
    if load_level == "high":
        print("Starting CPU load (high)...")
        env = os.environ.copy()
        env["CPUSET"] = cpuset
        subprocess.run(["./benchmarks/load.sh", "high"], env=env, check=True)
        time.sleep(12)  # wait for PSI avg10 to establish
    else:
        print("Ensuring clean idle state (load stopped)...")
        subprocess.run(["./benchmarks/load.sh", "stop"], check=False)
        time.sleep(1)

    # Start daemon
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
        daemon_cmd.extend(["--holdout-prob", "0.0"])  # 0 holdouts for smoke to see all claims

    print(f"Starting PreCompile daemon: {' '.join(daemon_cmd)}")
    log_fp = open(log_file, "w")
    daemon_proc = subprocess.Popen(
        daemon_cmd,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        text=True,
    )

    t_start = time.monotonic()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if pid_file.exists():
            break
        time.sleep(0.1)

    try:
        # Run developer workload simulation
        print(f"Running {cycles} save-and-build cycles...")
        sim_cmd = [
            sys.executable, "benchmarks/build_on_save.py",
            "--project", str(project_dir),
            "--cycles", str(cycles),
            "--edit-file", "big.c",
            "--delay-mean", "1.8",
            "--delay-jitter", "0.05",
            "--mode", "auto",
            "--cpuset", cpuset,
        ]
        sim_res = subprocess.run(sim_cmd, timeout=300, capture_output=True, text=True)
        print(f"Simulation completed. RC: {sim_res.returncode}")

    finally:
        t_end = time.monotonic()
        total_duration = max(0.1, t_end - t_start)

        # Stop load
        if load_level == "high":
            print("Stopping load...")
            subprocess.run(["./benchmarks/load.sh", "stop"], check=False)

        # Terminate daemon cleanly
        print("Stopping daemon...")
        if daemon_proc.poll() is None:
            daemon_proc.send_signal(signal.SIGTERM)
            try:
                daemon_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                daemon_proc.kill()
                daemon_proc.wait(timeout=2.0)
        log_fp.close()

    # Read log file for shutdown stats
    log_content = log_file.read_text(encoding="utf-8", errors="replace")
    scans = 0
    avg_scan_ms = 0.0
    max_scan_ms = 0.0
    normal_scans = 0
    intensified_scans = 0
    time_normal_s = 0.0
    time_intensified_s = 0.0

    match = re.search(
        r"Watcher stats: scans=(\d+), avg_scan=([\d\.]+)ms, max_scan=([\d\.]+)ms.*?"
        r"normal_scans=(\d+) \(([\d\.]+)s\), intensified_scans=(\d+) \(([\d\.]+)s\)",
        log_content,
    )
    if match:
        scans = int(match.group(1))
        avg_scan_ms = float(match.group(2))
        max_scan_ms = float(match.group(3))
        normal_scans = int(match.group(4))
        time_normal_s = float(match.group(5))
        intensified_scans = int(match.group(6))
        time_intensified_s = float(match.group(7))
    else:
        # Fallback regex without mode split
        match_simple = re.search(r"Watcher stats: scans=(\d+), avg_scan=([\d\.]+)ms, max_scan=([\d\.]+)ms", log_content)
        if match_simple:
            scans = int(match_simple.group(1))
            avg_scan_ms = float(match_simple.group(2))
            max_scan_ms = float(match_simple.group(3))

    scans_per_sec = scans / total_duration if total_duration > 0 else 0.0
    rate_normal = (normal_scans / time_normal_s) if time_normal_s > 0 else 0.0
    rate_intensified = (intensified_scans / time_intensified_s) if time_intensified_s > 0 else 0.0

    # Read reservations from database
    reservations = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM reservations WHERE project_id = 'heavy'")
        reservations = [dict(r) for r in cur.fetchall()]

    claims = [r for r in reservations if r.get("claimed_pid") is not None]
    claim_count = len(claims)

    latencies_ms = [
        r["claim_latency_s"] * 1000.0
        for r in claims
        if r.get("claim_latency_s") is not None
    ]

    if latencies_ms:
        mean_lat_ms = sum(latencies_ms) / len(latencies_ms)
        sorted_lat = sorted(latencies_ms)
        mid = len(sorted_lat) // 2
        median_lat_ms = (
            (sorted_lat[mid - 1] + sorted_lat[mid]) / 2.0
            if len(sorted_lat) % 2 == 0
            else sorted_lat[mid]
        )
    else:
        mean_lat_ms = None
        median_lat_ms = None

    leftovers = get_leftover_cgroups()
    print(f"Results for {condition} [{load_level}]:")
    print(f"  Total reservations in DB: {len(reservations)}")
    print(f"  Claims count:             {claim_count}")
    print(f"  Mean latency:             {mean_lat_ms:.2f} ms" if mean_lat_ms is not None else "  Mean latency: N/A")
    print(f"  Median latency:           {median_lat_ms:.2f} ms" if median_lat_ms is not None else "  Median latency: N/A")
    print(f"  Watcher avg scan:         {avg_scan_ms:.2f} ms")
    print(f"  Watcher overall rate:     {scans_per_sec:.1f} scans/s")
    print(f"  Watcher normal rate:      {rate_normal:.1f} scans/s ({normal_scans} scans, {time_normal_s:.1f}s)")
    print(f"  Watcher intensified rate: {rate_intensified:.1f} scans/s ({intensified_scans} scans, {time_intensified_s:.1f}s)")
    print(f"  Leftover cgroups:         {len(leftovers)}")

    return {
        "condition": condition,
        "load": load_level,
        "claims": claim_count,
        "mean_latency_ms": mean_lat_ms,
        "median_latency_ms": median_lat_ms,
        "avg_scan_ms": avg_scan_ms,
        "scans_per_sec": scans_per_sec,
        "rate_normal": rate_normal,
        "rate_intensified": rate_intensified,
        "normal_scans": normal_scans,
        "intensified_scans": intensified_scans,
        "leftover_cgroups": len(leftovers),
    }


def main():
    if os.geteuid() != 0:
        print("Error: smoke test requires root privileges for cgroup actuation.")
        sys.exit(1)

    project_dir = Path("/home/saketh/precompile-demo-heavy")
    if not project_dir.exists():
        print(f"Error: Project dir {project_dir} does not exist.")
        sys.exit(1)

    experiments = [
        ("c1-reactive", "idle"),
        ("c1-reactive", "high"),
        ("c2-full", "idle"),
        ("c2-full", "high"),
        ("c3-static", "idle"),
        ("c3-static", "high"),
    ]
    results = []

    try:
        for cond, load in experiments:
            res = run_condition_test(cond, load_level=load, project_dir=project_dir, cycles=5)
            results.append(res)
            time.sleep(2)
    finally:
        # Final cleanup check
        subprocess.run(["./benchmarks/load.sh", "stop"], check=False)
        subprocess.run([sys.executable, "-m", "precompile.cli", "cleanup"], check=False)

    print("\n" + "=" * 96)
    print("COMPARISON TABLE: STEADY-STATE CONDITIONS SMOKE TEST (IDLE & HIGH LOAD)")
    print("=" * 96)
    print(f"| {'Condition':<12} | {'Load':<6} | {'Claims':<6} | {'Mean Lat':<12} | {'Med Lat':<12} | {'Avg Scan':<10} | {'Overall S/s':<11} | {'Intensified S/s':<15} |")
    print(f"|{'-'*14}|{'-'*8}|{'-'*8}|{'-'*14}|{'-'*14}|{'-'*12}|{'-'*13}|{'-'*17}|")
    for r in results:
        mean_str = f"{r['mean_latency_ms']:.2f} ms" if r['mean_latency_ms'] is not None else "N/A"
        med_str = f"{r['median_latency_ms']:.2f} ms" if r['median_latency_ms'] is not None else "N/A"
        scan_str = f"{r['avg_scan_ms']:.2f} ms"
        sps_str = f"{r['scans_per_sec']:.1f}"
        int_str = f"{r['rate_intensified']:.1f}" if r['intensified_scans'] > 0 else "N/A (0 scans)"
        print(f"| {r['condition']:<12} | {r['load']:<6} | {r['claims']:<6} | {mean_str:<12} | {med_str:<12} | {scan_str:<10} | {sps_str:<11} | {int_str:<15} |")
    print("=" * 96)

    # Save results to JSON
    out_json = Path("/tmp/condition_smoke_results.json")
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Summary JSON written to {out_json}")


if __name__ == "__main__":
    main()
