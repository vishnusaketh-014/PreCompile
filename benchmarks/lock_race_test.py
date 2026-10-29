#!/usr/bin/env python3
"""Lock-level Concurrency Race Test for ReservationManager.try_claim (Step 11f-2).

Bypasses the single-threaded ProcessWatcher event loop.
Spawns N real OS child processes waiting on pipes.
Calls ReservationManager.try_claim simultaneously from N separate threads
synchronized by threading.Barrier(N), backed by the real cgroup controller (CgroupController).

Conditions tested:
  1. atomic_claim = True (ON)
  2. atomic_claim = False without hook (OFF)
  3. atomic_claim = False with interleave hook (OFF + Hook)

Criterion:
  - ON gives exactly 1 winner in 100% of at least 1,000 rounds for each N in {2, 8, 32}.
  - OFF with hook gives > 1 winner in at least one round.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from precompile.cgroup import CgroupController
from precompile.reservation import ReservationManager
from precompile.watcher import ProcessInfo, compute_t_start_monotonic, parse_stat_content

REPO_DIR = Path("/mnt/c/Os Project") if Path("/mnt/c/Os Project").exists() else Path(__file__).resolve().parent.parent


def get_git_commit() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_DIR), capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return "unknown"


def is_git_dirty() -> bool:
    try:
        r1 = subprocess.run(["git", "diff", "--quiet"], cwd=str(REPO_DIR), check=False)
        r2 = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(REPO_DIR), check=False)
        return (r1.returncode != 0) or (r2.returncode != 0)
    except Exception:
        return True


def interleave_hook():
    """Simulates thread preemption between check and set when atomic_claim=False."""
    time.sleep(0.0002)


def run_lock_race_cell(
    N: int,
    condition: str,
    rounds: int,
    backend: CgroupController,
    plan_commit: str,
    git_dirty: bool,
    jsonl_fp,
) -> Dict[str, Any]:
    """Execute a batch of rounds for a given N and condition."""
    atomic = (condition == "ON")
    hook = interleave_hook if condition == "OFF_HOOK" else None

    single_winner_count = 0
    dup_winner_count = 0
    zero_winner_count = 0

    for r in range(rounds):
        # Create ReservationManager for this round
        mgr = ReservationManager(
            cgroup_backend=backend,
            clock=time.monotonic,
            atomic_claim=atomic,
            claim_interleave_hook=hook,
            sweep_window_s=0.0,  # Sweeper not needed for lock race
        )

        project_id = f"race_proj_{r}"
        rec = mgr.arm(project_id, r, "default", time.monotonic(), 0.0, 10.0)
        mgr.tick()

        # Spawn N real child processes
        pipe_r, pipe_w = os.pipe()
        pids: List[int] = []
        for _ in range(N):
            pid = os.fork()
            if pid == 0:
                os.close(pipe_w)
                try:
                    os.read(pipe_r, 1)
                except Exception:
                    pass
                os.close(pipe_r)
                os._exit(0)
            pids.append(pid)

        # Inspect /proc to build valid ProcessInfo
        procs: List[ProcessInfo] = []
        for pid in pids:
            try:
                stat_text = Path(f"/proc/{pid}/stat").read_text()
                _, ppid, starttime_ticks, _ = parse_stat_content(stat_text)
                t_start, _ = compute_t_start_monotonic(starttime_ticks, time.monotonic())
                procs.append(
                    ProcessInfo(
                        pid=pid,
                        ppid=ppid,
                        starttime_ticks=starttime_ticks,
                        exe=Path("/usr/bin/gcc"),
                        cwd=Path("/tmp"),
                        cmdline=["gcc"],
                        t_seen_monotonic=time.monotonic(),
                        t_start_monotonic=t_start,
                    )
                )
            except Exception:
                # Fallback if process stat read failed
                procs.append(
                    ProcessInfo(
                        pid=pid,
                        ppid=os.getpid(),
                        starttime_ticks=0,
                        exe=Path("/usr/bin/gcc"),
                        cwd=Path("/tmp"),
                        cmdline=["gcc"],
                        t_seen_monotonic=time.monotonic(),
                        t_start_monotonic=time.monotonic(),
                    )
                )

        # Barrier synchronization for true simultaneous lock contention
        barrier = threading.Barrier(N)
        results = [False] * N

        def worker(idx: int):
            try:
                barrier.wait(timeout=2.0)
                results[idx] = mgr.try_claim(project_id, procs[idx])
            except Exception:
                results[idx] = False

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Unblock and reap children
        os.close(pipe_r)
        os.close(pipe_w)
        for p in pids:
            try:
                os.waitpid(p, 0)
            except ChildProcessError:
                pass

        # Cleanup cgroup
        mgr.cancel(rec.id)

        winners = sum(1 for res in results if res)
        if winners == 1:
            single_winner_count += 1
        elif winners > 1:
            dup_winner_count += 1
        else:
            zero_winner_count += 1

        rec_entry = {
            "round": r + 1,
            "plan_commit": plan_commit,
            "git_dirty": git_dirty,
            "N": N,
            "condition": condition,
            "winners": winners,
            "single_winner": (winners == 1),
            "duplicate_winner": (winners > 1),
        }
        jsonl_fp.write(json.dumps(rec_entry) + "\n")
        jsonl_fp.flush()

    return {
        "N": N,
        "condition": condition,
        "rounds": rounds,
        "single_winner_count": single_winner_count,
        "dup_winner_count": dup_winner_count,
        "zero_winner_count": zero_winner_count,
        "single_winner_rate": single_winner_count / rounds,
        "pass": (single_winner_count == rounds) if condition == "ON" else (dup_winner_count > 0),
    }


def main():
    parser = argparse.ArgumentParser(description="Lock-level race test for ReservationManager.")
    parser.add_argument("--rounds", type=int, default=1000, help="Number of rounds per cell (default: 1000).")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/results_step11f/lock_race_results.jsonl"),
        help="Path for raw JSONL results.",
    )
    args = parser.parse_args()

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("Error: Must run as root to access real cgroups.", file=sys.stderr)
        sys.exit(1)

    plan_commit = get_git_commit()
    git_dirty = is_git_dirty()
    if git_dirty:
        print(f"Error: Git working tree is dirty ({plan_commit}). Refusing to run.", file=sys.stderr)
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    backend = CgroupController(base_cgroup_path="/sys/fs/cgroup")

    n_values = [2, 8, 32]
    conditions = ["ON", "OFF_NOHOOK", "OFF_HOOK"]

    print("================================================================================")
    print("PreCompile Step 11f-2: Lock-Level Concurrency Race Validation")
    print(f"Plan Commit: {plan_commit} | Working Tree Clean: {not git_dirty}")
    print(f"Rounds per Cell: {args.rounds} | Real Cgroup Backend: /sys/fs/cgroup")
    print("================================================================================")

    results = []
    with open(args.output, "w", encoding="utf-8") as jsonl_fp:
        for N in n_values:
            for cond in conditions:
                print(f"\n>>> Running Lock Race: N={N:<2} | Condition={cond:<10} ({args.rounds} rounds) ...")
                t0 = time.time()
                summary = run_lock_race_cell(N, cond, args.rounds, backend, plan_commit, git_dirty, jsonl_fp)
                dur = time.time() - t0
                results.append(summary)
                print(f"    Completed in {dur:.2f}s: 1-Winner={summary['single_winner_count']}/{args.rounds} | "
                      f"Duplicates={summary['dup_winner_count']}/{args.rounds} | Zero={summary['zero_winner_count']} | "
                      f"Pass={summary['pass']}")

    print("\n================================================================================")
    print("SUMMARY TABLE: Lock-Level Concurrency Race")
    print("================================================================================")
    print(f"{'N':<4} | {'Condition':<12} | {'Rounds':<8} | {'Exactly 1 Winner':<18} | {'Duplicates':<12} | {'Status':<6}")
    print("-" * 75)
    for s in results:
        status_str = "PASS" if s["pass"] else "FAIL"
        print(f"{s['N']:<4} | {s['condition']:<12} | {s['rounds']:<8} | {s['single_winner_count']:<18} | {s['dup_winner_count']:<12} | {status_str:<6}")


if __name__ == "__main__":
    main()
