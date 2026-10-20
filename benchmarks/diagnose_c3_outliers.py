#!/usr/bin/env python3
"""Focused diagnostic experiment on partial-boost outliers under high load.

Runs 40 builds of C3-static under high load with opt-in descendant probes enabled (+0.5s, +1.5s).
Records cpu_share, weight readbacks, cpu.stat, claim latency, and descendant leak probes.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = Path("/home/saketh/precompile-demo-heavy")
OUT_DIR = SCRIPT_DIR / "results_c3_diagnostics"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def read_psi_cpu_avg10() -> float:
    try:
        with open("/proc/pressure/cpu", "r") as f:
            for line in f:
                if line.startswith("some"):
                    m = re.search(r"avg10=([\d\.]+)", line)
                    if m:
                        return float(m.group(1))
    except Exception:
        pass
    return 0.0


def main():
    print("=" * 80)
    print("PreCompile Step 11a4: Diagnostic Run - 40 Builds C3-static at High Load")
    print("Investigating partial-boost outliers (cpu_share < 0.50) with descendant probes")
    print("=" * 80)

    # 1. Clean system
    subprocess.run([str(SCRIPT_DIR / "load.sh"), "stop"], check=False)
    subprocess.run([sys.executable, "-m", "precompile.cli", "cleanup"], check=False)

    db_path = OUT_DIR / "c3_diagnostics.db"
    pid_file = OUT_DIR / "c3_diagnostics.pid"
    log_file = OUT_DIR / "c3_diagnostics.log"

    for f in [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm"), pid_file, log_file]:
        if f.exists():
            f.unlink()

    # Pre-compile project
    run_cmd(["make", "-C", str(PROJECT_DIR), "clean"], check=False)
    run_cmd(["make", "-C", str(PROJECT_DIR)], check=True)

    run_cmd([
        sys.executable, "-m", "precompile.cli", "projects", "add",
        "--id", "heavy",
        "--path", str(PROJECT_DIR),
        "--tools", "gcc,make",
        "--db", str(db_path),
    ], check=True)

    # 2. Start daemon with leak check enabled
    cmd = [
        sys.executable, "-m", "precompile.cli", "daemon",
        "--actuate",
        "--weight", "500",
        "--condition", "c3-static",
        "--db", str(db_path),
        "--pid-file", str(pid_file),
        "--log-level", "INFO",
        "--leak-check",
    ]
    log_fp = open(log_file, "w")
    daemon_proc = subprocess.Popen(cmd, stdout=log_fp, stderr=subprocess.STDOUT, text=True)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if pid_file.exists():
            break
        time.sleep(0.05)

    # 3. Start high load
    env = os.environ.copy()
    env["CPUSET"] = "0-1"
    print("Starting high background contention load (stress-ng 4 hogs/CPU)...")
    subprocess.run([str(SCRIPT_DIR / "load.sh"), "high"], env=env, check=True)
    print("Waiting 12s for PSI cpu stabilization...")
    time.sleep(12)

    build_results: List[Dict[str, Any]] = []

    try:
        import sqlite3
        last_build_id = 0
        last_res_rowid = 0

        for b_i in range(1, 41):
            psi_start = read_psi_cpu_avg10()
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
            dur_wall = time.monotonic() - t0

            m_bwall = re.search(r"build_wall_s=([\d\.]+)", res.stdout)
            build_wall_s = float(m_bwall.group(1)) if m_bwall else dur_wall

            m_cpu = re.search(r"cpu=([\d\.]+)s", res.stdout)
            cpu_s = float(m_cpu.group(1)) if m_cpu else 0.0

            # Query DB
            b_dict = {}
            r_dict = {}
            if db_path.exists():
                with sqlite3.connect(db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM builds WHERE id > ? ORDER BY id DESC LIMIT 1", (last_build_id,))
                    row = cur.fetchone()
                    if row:
                        b_dict = dict(row)
                        last_build_id = b_dict.get("id", last_build_id)
                    cur.execute("SELECT rowid, * FROM reservations WHERE rowid > ? ORDER BY rowid DESC LIMIT 1", (last_res_rowid,))
                    row = cur.fetchone()
                    if row:
                        r_dict = dict(row)
                        last_res_rowid = r_dict.get("rowid", last_res_rowid)

            lat_s = r_dict.get("claim_latency_s")
            usage_usec = r_dict.get("cgroup_usage_usec")
            cgroup_share = (usage_usec / (1e6 * build_wall_s)) if (usage_usec is not None and build_wall_s > 0) else None

            # Check probe logs from log_file for this reservation / claimed_pid
            claimed_pid = r_dict.get("claimed_pid")
            res_id = r_dict.get("id")

            # Brief sleep to allow +1.5s probe to finish logging
            time.sleep(1.6)

            log_text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
            probe_lines = [
                line.strip() for line in log_text.splitlines()
                if f"claimed_pid={claimed_pid}" in line or (res_id and f"res={res_id}" in line)
            ]

            weight_info = None
            stat_info = None
            for pl in probe_lines:
                if "[cgroup_weight]" in pl or "[cgroup_cpu]" in pl:
                    if "weight_claim=" in pl or "weight_at_claim=" in pl:
                        weight_info = pl

            leaks_05 = None
            leaks_15 = None
            for pl in probe_lines:
                if "[leak] +0.5s" in pl:
                    leaks_05 = pl
                elif "[leak] +1.5s" in pl:
                    leaks_15 = pl

            rec = {
                "build_index": b_i,
                "build_wall_s": build_wall_s,
                "cpu_s": cpu_s,
                "cpu_share": cgroup_share,
                "claim_latency_ms": (lat_s * 1000.0) if lat_s is not None else None,
                "claimed_pid": claimed_pid,
                "psi_start": psi_start,
                "weight_info": weight_info,
                "leaks_05": leaks_05,
                "leaks_15": leaks_15,
                "is_outlier": (cgroup_share is not None and cgroup_share < 0.50),
            }
            build_results.append(rec)
            status_tag = "OUTLIER (share < 0.50)" if rec["is_outlier"] else "OK"
            share_str = f"{cgroup_share*100:.1f}%" if cgroup_share is not None else "N/A"
            print(f"Build {b_i:02d}/40: wall={build_wall_s:.3f}s | share={share_str} | lat={rec['claim_latency_ms']}ms | psi={psi_start:.1f}% | {status_tag}")
            if rec["is_outlier"]:
                print(f"  --> Outlier detail: leaks_05={leaks_05} | leaks_15={leaks_15} | weight={weight_info}")
    finally:
        # Stop load and daemon
        subprocess.run([str(SCRIPT_DIR / "load.sh"), "stop"], check=False)
        if daemon_proc.poll() is None:
            daemon_proc.terminate()
            try:
                daemon_proc.wait(timeout=3.0)
            except Exception:
                daemon_proc.kill()
        subprocess.run([sys.executable, "-m", "precompile.cli", "cleanup"], check=False)

    # Summary
    outliers = [r for r in build_results if r["is_outlier"]]
    shares = [r["cpu_share"] for r in build_results if r["cpu_share"] is not None]
    from precompile.stats import median

    print("\n" + "=" * 90)
    print("C3-STATIC 40-BUILD OUTLIER DIAGNOSTIC SUMMARY")
    print("=" * 90)
    print(f"Total Builds:               40")
    print(f"Median Build Duration:      {median([r['build_wall_s'] for r in build_results]):.3f}s")
    print(f"Median Cgroup CPU Share:    {median(shares)*100:.1f}%" if shares else "N/A")
    print(f"Outliers with Share < 50%:  {len(outliers)} / 40 ({len(outliers)/40*100:.1f}%)")
    print("=" * 90)

    if outliers:
        print("\nDETAILED PROBE EVIDENCE FOR PARTIAL-BOOST OUTLIERS:")
        for o in outliers:
            print(f"- Build #{o['build_index']}: wall={o['build_wall_s']:.3f}s, cpu={o['cpu_s']:.2f}s, share={o['cpu_share']*100:.1f}%, lat={o['claim_latency_ms']:.1f}ms, psi0={o['psi_start']:.1f}%")
            print(f"    0.5s Probe: {o['leaks_05']}")
            print(f"    1.5s Probe: {o['leaks_15']}")
            print(f"    Weight/Stat: {o['weight_info']}")
    else:
        print("No builds with cpu_share < 0.50 observed in this 40-build run.")

    with open(OUT_DIR / "c3_diagnostic_results.json", "w", encoding="utf-8") as f:
        json.dump(build_results, f, indent=2)


if __name__ == "__main__":
    main()
