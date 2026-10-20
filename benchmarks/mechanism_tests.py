#!/usr/bin/env python3
"""Mechanism verification tests for PreCompile.

Stage Mapping: Step 11a2 Item 5.
Tests:
1. Concurrency Double-Claim Test:
   Runs N rounds with atomic_claim=True and N rounds with atomic_claim=False (with
   interleaving hook). Verifies that atomic_claim=True eliminates double claims,
   whereas atomic_claim=False produces double claims.
2. Holdout Agreement Test:
   With full system C2 at holdout_prob=0.5 under high load, collects >= 30 reserved
   and >= 30 holdout builds, computes self-measured effect with 95% bootstrap CI,
   and compares it with controlled comparison (30 C0 builds vs 30 C2 builds).
   Verifies whether the self-measured holdout effect agrees with the controlled delta.
"""

import argparse
import json
import math
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from precompile.cgroup import FakeCgroupBackend
from precompile.gates import ArmingGate
from precompile.reservation import ReservationManager, ReservationState
from precompile.stats import bootstrap_relative_effect_ci, median
from precompile.watcher import ProcessInfo

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
DEFAULT_PROJECT_DIR = Path("/home/saketh/precompile-demo-heavy")
DEFAULT_CPUSET = "0-1"


def run_concurrency_rounds(rounds: int = 2000) -> Dict[str, Any]:
    print(f"\n================================================================================")
    print(f"Running Concurrency Tests: Atomic, Natural Race, and Hook-Forced Race")
    print(f"================================================================================")

    # 1. Mode: atomic_claim = True (unhooked, full rounds)
    print(f"1. Evaluating atomic_claim=True ({rounds} rounds, 5 threads released by barrier)...")
    atomic_single = 0
    atomic_double = 0

    for r in range(rounds):
        backend = FakeCgroupBackend()
        manager = ReservationManager(
            cgroup_backend=backend,
            atomic_claim=True,
            starttime_reader=lambda pid: 1000 + (pid - 20000),
        )
        res = manager.arm(
            project_id="concurrency_proj",
            prediction_id=r + 1,
            tool_profile="default",
            t_save_mono=time.monotonic(),
            window_lo=0.0,
            window_hi=10.0,
            median_build_dur=5.0,
            weight=500,
            reason="concurrency_test",
        )

        num_threads = 5
        barrier = threading.Barrier(num_threads)
        results: List[bool] = []
        lock = threading.Lock()

        def _worker_atomic(tid: int):
            proc = ProcessInfo(
                pid=20000 + tid,
                ppid=1,
                starttime_ticks=1000 + tid,
                exe=Path("/usr/bin/gcc"),
                cwd=Path("/tmp"),
                cmdline=["gcc", "-c", "test.c"],
                t_seen_monotonic=time.monotonic(),
                t_start_monotonic=time.monotonic(),
            )
            barrier.wait()
            claimed = manager.try_claim("concurrency_proj", proc, {proc.pid: proc})
            with lock:
                results.append(claimed)

        threads = [threading.Thread(target=_worker_atomic, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        succ = sum(1 for x in results if x is True)
        if succ == 1:
            atomic_single += 1
        elif succ > 1:
            atomic_double += 1

    # 2. Mode: atomic_claim = False (Natural Race without hook, full rounds)
    print(f"2. Evaluating atomic_claim=False Natural Race ({rounds} rounds, 5 threads released by barrier, no hook)...")
    natural_single = 0
    natural_double = 0

    for r in range(rounds):
        backend = FakeCgroupBackend()
        manager = ReservationManager(
            cgroup_backend=backend,
            atomic_claim=False,
            claim_interleave_hook=None,
            starttime_reader=lambda pid: 1000 + (pid - 20000),
        )
        res = manager.arm(
            project_id="concurrency_proj",
            prediction_id=r + 1,
            tool_profile="default",
            t_save_mono=time.monotonic(),
            window_lo=0.0,
            window_hi=10.0,
            median_build_dur=5.0,
            weight=500,
            reason="concurrency_test",
        )

        num_threads = 5
        barrier = threading.Barrier(num_threads)
        results = []
        lock = threading.Lock()

        def _worker_natural(tid: int):
            proc = ProcessInfo(
                pid=20000 + tid,
                ppid=1,
                starttime_ticks=1000 + tid,
                exe=Path("/usr/bin/gcc"),
                cwd=Path("/tmp"),
                cmdline=["gcc", "-c", "test.c"],
                t_seen_monotonic=time.monotonic(),
                t_start_monotonic=time.monotonic(),
            )
            barrier.wait()
            claimed = manager.try_claim("concurrency_proj", proc, {proc.pid: proc})
            with lock:
                results.append(claimed)

        threads = [threading.Thread(target=_worker_natural, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        succ = sum(1 for x in results if x is True)
        if succ == 1:
            natural_single += 1
        elif succ > 1:
            natural_double += 1

    # 3. Mode: atomic_claim = False (Hook-Forced Interleaving, 200 rounds)
    forced_rounds = min(200, rounds)
    print(f"3. Evaluating atomic_claim=False Hook-Forced Race ({forced_rounds} rounds, sleep hook)...")
    forced_single = 0
    forced_double = 0

    for r in range(forced_rounds):
        backend = FakeCgroupBackend()
        manager = ReservationManager(
            cgroup_backend=backend,
            atomic_claim=False,
            claim_interleave_hook=lambda: time.sleep(0.0005),
            starttime_reader=lambda pid: 1000 + (pid - 20000),
        )
        res = manager.arm(
            project_id="concurrency_proj",
            prediction_id=r + 1,
            tool_profile="default",
            t_save_mono=time.monotonic(),
            window_lo=0.0,
            window_hi=10.0,
            median_build_dur=5.0,
            weight=500,
            reason="concurrency_test",
        )

        num_threads = 5
        barrier = threading.Barrier(num_threads)
        results = []
        lock = threading.Lock()

        def _worker_forced(tid: int):
            proc = ProcessInfo(
                pid=20000 + tid,
                ppid=1,
                starttime_ticks=1000 + tid,
                exe=Path("/usr/bin/gcc"),
                cwd=Path("/tmp"),
                cmdline=["gcc", "-c", "test.c"],
                t_seen_monotonic=time.monotonic(),
                t_start_monotonic=time.monotonic(),
            )
            barrier.wait()
            claimed = manager.try_claim("concurrency_proj", proc, {proc.pid: proc})
            with lock:
                results.append(claimed)

        threads = [threading.Thread(target=_worker_forced, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        succ = sum(1 for x in results if x is True)
        if succ == 1:
            forced_single += 1
        elif succ > 1:
            forced_double += 1

    print("\nConcurrency Test Summary:")
    print(f"  atomic_claim=True  ({rounds} rounds): Exactly 1 claim: {atomic_single}/{rounds} ({atomic_single/rounds*100:.2f}%), Double claims: {atomic_double}/{rounds} ({atomic_double/rounds*100:.2f}%)")
    print(f"  Natural Race       ({rounds} rounds): Exactly 1 claim: {natural_single}/{rounds} ({natural_single/rounds*100:.2f}%), Double claims: {natural_double}/{rounds} ({natural_double/rounds*100:.2f}%)")
    print(f"  Hook-Forced Race   ({forced_rounds} rounds): Exactly 1 claim: {forced_single}/{forced_rounds} ({forced_single/forced_rounds*100:.2f}%), Double claims: {forced_double}/{forced_rounds} ({forced_double/forced_rounds*100:.2f}%)")

    assert atomic_double == 0, f"Expected 0 double claims with atomic_claim=True, got {atomic_double}"
    assert forced_double > 0, f"Expected >0 double claims with forced hook, got {forced_double}"

    return {
        "status": "PASSED",
        "rounds": rounds,
        "atomic_claim_true": {"single_claims": atomic_single, "double_claims": atomic_double, "double_claim_rate": atomic_double / rounds},
        "atomic_claim_false_natural": {"single_claims": natural_single, "double_claims": natural_double, "double_claim_rate": natural_double / rounds},
        "atomic_claim_false_forced": {"rounds": forced_rounds, "single_claims": forced_single, "double_claims": forced_double, "double_claim_rate": forced_double / forced_rounds},
    }


def run_holdout_agreement_test(
    n_targets: int = 30,
    project_dir: Path = DEFAULT_PROJECT_DIR,
    cpuset: str = DEFAULT_CPUSET,
) -> Dict[str, Any]:
    print(f"\n================================================================================")
    print(f"Running Holdout Agreement Test (>= {n_targets} builds per arm under High Load)")
    print(f"================================================================================")

    load_script = SCRIPT_DIR / "load.sh"

    # Pre-build initial artifacts
    subprocess.run(["make", "-C", str(project_dir), "clean"], check=False, stdout=subprocess.DEVNULL)
    subprocess.run(["make", "-C", str(project_dir)], check=True, stdout=subprocess.DEVNULL)

    # 1. Start high load
    print("Starting high CPU load...")
    env = os.environ.copy()
    env["CPUSET"] = cpuset
    subprocess.run([str(load_script), "high"], env=env, check=True)
    time.sleep(12)

    db_holdout = Path("/tmp/holdout_agreement.db")
    pid_holdout = Path("/tmp/holdout_agreement.pid")
    log_holdout = Path("/tmp/holdout_agreement.log")

    for f in [db_holdout, Path(f"{db_holdout}-wal"), Path(f"{db_holdout}-shm"), pid_holdout, log_holdout]:
        if f.exists():
            f.unlink()

    # Register project
    subprocess.run([
        sys.executable, "-m", "precompile.cli", "projects", "add",
        "--id", "heavy",
        "--path", str(project_dir),
        "--tools", "gcc,make",
        "--db", str(db_holdout),
    ], check=True)

    # Pre-seed warm model
    from precompile.store import DataStore
    with DataStore(db_path=db_holdout) as store:
        for i in range(6):
            t_s = time.time() - (6 - i) * 10
            store.record_sample("heavy", "source", "gcc,make", 1.8, accepted=True, t_save_wall=t_s)
            pid = store.record_prediction("heavy", "source", "gcc,make", t_s, 1.4, 2.2, armed=True, holdout=False)
            store.score_prediction(pid, outcome="hit", observed_delay_s=1.8)
            store.record_build("heavy", "observed", 0.0, 0.5, t_s, t_s + 0.5, prediction_id=pid)
        store.save_model_state(("heavy", "source", "gcc,make"), 6, 1.80, 0.01)

    # Start C2 daemon with holdout_prob = 0.50
    cmd_c2 = [
        sys.executable, "-m", "precompile.cli", "daemon",
        "--condition", "c2-full",
        "--actuate",
        "--weight", "500",
        "--holdout-prob", "0.50",
        "--db", str(db_holdout),
        "--pid-file", str(pid_holdout),
        "--log-level", "INFO",
    ]
    log_fp = open(log_holdout, "w")
    daemon_c2 = subprocess.Popen(cmd_c2, stdout=log_fp, stderr=subprocess.STDOUT, text=True)

    # Wait for daemon
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if pid_holdout.exists():
            break
        time.sleep(0.05)

    reserved_durations: List[float] = []
    holdout_durations: List[float] = []

    try:
        print(f"Collecting C2 runs (target: >= {n_targets} reserved and >= {n_targets} holdouts)...")
        cycle = 0
        while len(reserved_durations) < n_targets or len(holdout_durations) < n_targets:
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
            subprocess.run(sim_cmd, timeout=60, check=True, capture_output=True)
            dur = time.monotonic() - t0

            # Query DB
            with sqlite3.connect(db_holdout) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT * FROM builds ORDER BY id DESC LIMIT 1")
                b_row = cur.fetchone()

            if b_row:
                b_cond = b_row["condition"]
                b_dur = b_row["duration_s"]
                if b_cond == "reserved":
                    reserved_durations.append(b_dur)
                elif b_cond == "holdout":
                    holdout_durations.append(b_dur)
            print(f"  Holdout cycle {cycle:>2}: Reserved={len(reserved_durations)}/{n_targets}, Holdouts={len(holdout_durations)}/{n_targets}")

    finally:
        if daemon_c2.poll() is None:
            daemon_c2.send_signal(signal.SIGTERM)
            try:
                daemon_c2.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                daemon_c2.kill()
                daemon_c2.wait(timeout=2.0)
        log_fp.close()

    # 2. Run Controlled Comparison: 30 C0 vs 30 C2 (100% boosted)
    print(f"\nRunning Controlled Comparison: {n_targets} C0 builds vs {n_targets} C2 builds...")

    # C0 builds
    db_c0 = Path("/tmp/c0_comp.db")
    pid_c0 = Path("/tmp/c0_comp.pid")
    log_c0 = Path("/tmp/c0_comp.log")
    for f in [db_c0, Path(f"{db_c0}-wal"), Path(f"{db_c0}-shm"), pid_c0, log_c0]:
        if f.exists():
            f.unlink()

    subprocess.run([
        sys.executable, "-m", "precompile.cli", "projects", "add",
        "--id", "heavy",
        "--path", str(project_dir),
        "--tools", "gcc,make",
        "--db", str(db_c0),
    ], check=True)

    cmd_c0 = [
        sys.executable, "-m", "precompile.cli", "daemon",
        "--observe-only",
        "--db", str(db_c0),
        "--pid-file", str(pid_c0),
        "--log-level", "INFO",
    ]
    log_fp_c0 = open(log_c0, "w")
    daemon_c0 = subprocess.Popen(cmd_c0, stdout=log_fp_c0, stderr=subprocess.STDOUT, text=True)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if pid_c0.exists():
            break
        time.sleep(0.05)

    c0_durations: List[float] = []
    try:
        print(f"  Running {n_targets} C0 builds...")
        for i in range(n_targets):
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
            subprocess.run(sim_cmd, timeout=60, check=True, capture_output=True)
            dur = time.monotonic() - t0
            with sqlite3.connect(db_c0) as conn:
                cur = conn.cursor()
                cur.execute("SELECT duration_s FROM builds ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                c0_durations.append(row[0] if row else dur)
    finally:
        if daemon_c0.poll() is None:
            daemon_c0.send_signal(signal.SIGTERM)
            try:
                daemon_c0.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                daemon_c0.kill()
                daemon_c0.wait(timeout=2.0)
        log_fp_c0.close()

    # C2 builds (100% boosted)
    db_c2 = Path("/tmp/c2_comp.db")
    pid_c2 = Path("/tmp/c2_comp.pid")
    log_c2 = Path("/tmp/c2_comp.log")
    for f in [db_c2, Path(f"{db_c2}-wal"), Path(f"{db_c2}-shm"), pid_c2, log_c2]:
        if f.exists():
            f.unlink()

    subprocess.run([
        sys.executable, "-m", "precompile.cli", "projects", "add",
        "--id", "heavy",
        "--path", str(project_dir),
        "--tools", "gcc,make",
        "--db", str(db_c2),
    ], check=True)

    with DataStore(db_path=db_c2) as store:
        for i in range(6):
            t_s = time.time() - (6 - i) * 10
            store.record_sample("heavy", "source", "gcc,make", 1.8, accepted=True, t_save_wall=t_s)
            pid = store.record_prediction("heavy", "source", "gcc,make", t_s, 1.4, 2.2, armed=True, holdout=False)
            store.score_prediction(pid, outcome="hit", observed_delay_s=1.8)
            store.record_build("heavy", "observed", 0.0, 0.5, t_s, t_s + 0.5, prediction_id=pid)
        store.save_model_state(("heavy", "source", "gcc,make"), 6, 1.80, 0.01)

    cmd_c2_ctrl = [
        sys.executable, "-m", "precompile.cli", "daemon",
        "--condition", "c2-full",
        "--actuate",
        "--weight", "500",
        "--holdout-prob", "0.0",
        "--db", str(db_c2),
        "--pid-file", str(pid_c2),
        "--log-level", "INFO",
    ]
    log_fp_c2 = open(log_c2, "w")
    daemon_c2_ctrl = subprocess.Popen(cmd_c2_ctrl, stdout=log_fp_c2, stderr=subprocess.STDOUT, text=True)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if pid_c2.exists():
            break
        time.sleep(0.05)

    c2_durations: List[float] = []
    try:
        print(f"  Running {n_targets} C2 builds...")
        for i in range(n_targets):
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
            subprocess.run(sim_cmd, timeout=60, check=True, capture_output=True)
            dur = time.monotonic() - t0
            with sqlite3.connect(db_c2) as conn:
                cur = conn.cursor()
                cur.execute("SELECT duration_s FROM builds ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                c2_durations.append(row[0] if row else dur)
    finally:
        if daemon_c2_ctrl.poll() is None:
            daemon_c2_ctrl.send_signal(signal.SIGTERM)
            try:
                daemon_c2_ctrl.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                daemon_c2_ctrl.kill()
                daemon_c2_ctrl.wait(timeout=2.0)
        log_fp_c2.close()

        # Stop load and cleanup
        subprocess.run([str(load_script), "stop"], check=False)
        time.sleep(2)
        subprocess.run([sys.executable, "-m", "precompile.cli", "cleanup"], check=False)

    # 3. Statistical Comparison
    # A. Holdout Effect: 1 - (med(reserved) / med(holdout))
    med_res = median(reserved_durations)
    med_hld = median(holdout_durations)
    eff_holdout = 1.0 - (med_res / med_hld) if med_hld > 0 else 0.0
    _, ci_hld_lo, ci_hld_hi = bootstrap_relative_effect_ci(
        {"all": reserved_durations}, {"all": holdout_durations}, n_resamples=1000
    )

    # B. Controlled Effect: 1 - (med(C2) / med(C0))
    med_c0 = median(c0_durations)
    med_c2 = median(c2_durations)
    eff_ctrl = 1.0 - (med_c2 / med_c0) if med_c0 > 0 else 0.0
    _, ci_ctrl_lo, ci_ctrl_hi = bootstrap_relative_effect_ci(
        {"all": c2_durations}, {"all": c0_durations}, n_resamples=1000
    )

    # Check CI overlap
    intervals_overlap = (ci_hld_lo <= ci_ctrl_hi) and (ci_ctrl_lo <= ci_hld_hi)

    print("\n" + "=" * 90)
    print("HOLDOUT AGREEMENT TEST RESULTS (HIGH LOAD)")
    print("=" * 90)
    print(f"1. Self-Measured Holdout Effect (N_res={len(reserved_durations)}, N_hld={len(holdout_durations)}):")
    print(f"   Reserved Med: {med_res:.2f}s | Holdout Med: {med_hld:.2f}s")
    print(f"   Effect:       {eff_holdout*100:+.1f}% (95% Bootstrap CI: [{ci_hld_lo*100:+.1f}%, {ci_hld_hi*100:+.1f}%])")
    print(f"\n2. Controlled Comparison (N_C0={len(c0_durations)}, N_C2={len(c2_durations)}):")
    print(f"   C0 Med:       {med_c0:.2f}s | C2 Med:      {med_c2:.2f}s")
    print(f"   Effect:       {eff_ctrl*100:+.1f}% (95% Bootstrap CI: [{ci_ctrl_lo*100:+.1f}%, {ci_ctrl_hi*100:+.1f}%])")
    print(f"\n3. Agreement Conclusion:")
    print(f"   Confidence Intervals Overlap: {'YES' if intervals_overlap else 'NO'}")
    print(f"   Status: {'AGREE' if intervals_overlap else 'DISAGREE'}")
    print("=" * 90)

    return {
        "status": "PASSED" if intervals_overlap else "DISAGREE",
        "holdout": {
            "n_reserved": len(reserved_durations),
            "n_holdout": len(holdout_durations),
            "med_reserved": med_res,
            "med_holdout": med_hld,
            "effect": eff_holdout,
            "ci_low": ci_hld_lo,
            "ci_high": ci_hld_hi,
        },
        "controlled": {
            "n_c0": len(c0_durations),
            "n_c2": len(c2_durations),
            "med_c0": med_c0,
            "med_c2": med_c2,
            "effect": eff_ctrl,
            "ci_low": ci_ctrl_lo,
            "ci_high": ci_ctrl_hi,
        },
        "intervals_overlap": intervals_overlap,
    }


def main():
    parser = argparse.ArgumentParser(description="PreCompile Mechanism Tests")
    parser.add_argument("--mode", choices=["concurrency", "holdout", "all"], default="all", help="Test mode to run")
    parser.add_argument("--rounds", type=int, default=200, help="Rounds for concurrency test")
    parser.add_argument("--builds", type=int, default=30, help="Builds per arm for holdout agreement test")
    parser.add_argument("--project-dir", default=str(DEFAULT_PROJECT_DIR), help="Heavy project directory")
    args = parser.parse_args()

    results: Dict[str, Any] = {}
    if args.mode in ("concurrency", "all"):
        results["concurrency"] = run_concurrency_rounds(rounds=args.rounds)

    if args.mode in ("holdout", "all"):
        if os.geteuid() != 0:
            print("Error: Holdout agreement test requires root privileges for actuation and load.", file=sys.stderr)
            sys.exit(1)
        results["holdout"] = run_holdout_agreement_test(n_targets=args.builds, project_dir=Path(args.project_dir))

    out_file = Path("/tmp/mechanism_tests_results.json")
    out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nResults saved to {out_file}")


if __name__ == "__main__":
    main()
