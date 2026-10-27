#!/usr/bin/env python3
"""Concurrent Process Claiming Evaluation Launcher (Step 11f).

Stage Mapping: Step 11f - Validate concurrent process claiming.
Pre-registered protocol evaluating variants V1-V4 across N in {2, 8, 32}
under atomic claim ON and atomic claim OFF (negative control).

Enforces:
- Git clean tree assertion at launch.
- Shared-pipe barrier launcher for near-simultaneous candidate process creation.
- Deterministic reservation arming before candidate release.
- Live sampling of cgroup.procs, descendant sweeper validation, and DB verification.
- Output written directly to raw JSONL with complete audit header.
"""

import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
WORKSPACE_BASE = Path("/tmp/precompile_concurrent_11f")


def get_git_metadata() -> Dict[str, Any]:
    """Inspect git repository state and enforce clean working tree."""
    commit = "unknown"
    is_dirty = False
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_DIR), capture_output=True, text=True, check=True)
        commit = res.stdout.strip()
    except Exception:
        pass

    try:
        res_diff = subprocess.run(["git", "diff", "--quiet"], cwd=str(REPO_DIR), check=False)
        res_cached = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(REPO_DIR), check=False)
        is_dirty = (res_diff.returncode != 0) or (res_cached.returncode != 0)
    except Exception:
        pass

    return {"git_commit": commit, "git_dirty": is_dirty}


def compile_helper_tools(bin_dir: Path) -> Tuple[Path, Path]:
    """Compile the C candidate sleeper and forker binaries."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    sleeper_path = bin_dir / "gcc_sleeper"
    forker_path = bin_dir / "gcc_forker"

    # Sleeper C source: sleeps for duration given in argv[1] (default 0.25s)
    sleeper_c = b"""
#include <unistd.h>
#include <stdlib.h>
int main(int argc, char** argv) {
    double d = (argc > 1 ? atof(argv[1]) : 0.25);
    usleep((useconds_t)(d * 1000000.0));
    return 0;
}
"""
    subprocess.run(["gcc", "-x", "c", "-", "-o", str(sleeper_path)], input=sleeper_c, check=True)
    os.chmod(str(sleeper_path), 0o755)

    # Forker C source: forks 4 children immediately upon start; all sleep for argv[1]
    forker_c = b"""
#include <unistd.h>
#include <stdlib.h>
#include <sys/wait.h>
int main(int argc, char** argv) {
    double d = (argc > 1 ? atof(argv[1]) : 0.25);
    pid_t children[4];
    for (int i = 0; i < 4; i++) {
        pid_t p = fork();
        if (p == 0) {
            usleep((useconds_t)(d * 1000000.0));
            _exit(0);
        } else if (p > 0) {
            children[i] = p;
        }
    }
    usleep((useconds_t)(d * 1000000.0));
    for (int i = 0; i < 4; i++) {
        waitpid(children[i], NULL, 0);
    }
    return 0;
}
"""
    subprocess.run(["gcc", "-x", "c", "-", "-o", str(forker_path)], input=forker_c, check=True)
    os.chmod(str(forker_path), 0o755)

    return sleeper_path, forker_path


def get_all_descendants_proc(root_pid: int, proc_path: Path = Path("/proc")) -> Set[int]:
    """Traverse /proc to find all live descendants of a PID."""
    descendants: Set[int] = set()
    queue = [root_pid]
    visited = {root_pid}

    # Build parent -> children map from current /proc entries
    children_map: Dict[int, List[int]] = {}
    try:
        for entry in proc_path.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            stat_file = entry / "stat"
            try:
                raw = stat_file.read_text(encoding="utf-8")
                # Parse ppid (field 4 after comm)
                after = raw.rsplit(")", 1)[1].split()
                ppid = int(after[1])
                children_map.setdefault(ppid, []).append(pid)
            except OSError:
                continue
    except OSError:
        pass

    while queue:
        curr = queue.pop(0)
        for child in children_map.get(curr, []):
            if child not in visited:
                visited.add(child)
                descendants.add(child)
                queue.append(child)

    return descendants


class ConcurrencyExperimentRunner:
    def __init__(
        self,
        trials_per_cell: int = 100,
        output_path: Path = Path("benchmarks/results_step11f/concurrent_claiming.jsonl"),
        quick_smoke: bool = False,
    ) -> None:
        self.trials_per_cell = 2 if quick_smoke else trials_per_cell
        self.output_path = output_path
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.git_meta = get_git_metadata()

        if self.git_meta["git_dirty"]:
            print(f"Error: Git working tree is dirty ({self.git_meta['git_commit']}). Refusing to run Step 11f.", file=sys.stderr)
            sys.exit(1)

        self.plan_commit = self.git_meta["git_commit"]
        self.ws_dir = WORKSPACE_BASE
        self.proj_target = self.ws_dir / "proj_target"
        self.proj_decoy = self.ws_dir / "proj_decoy"
        self.outside_dir = self.ws_dir / "outside_dir"
        self.bin_dir = self.ws_dir / "bin"
        self.db_path = self.ws_dir / "precompile.db"
        self.pid_file = self.ws_dir / "precompile.pid"
        self.log_file = self.ws_dir / "precompile.log"

    def setup_workspace(self) -> None:
        """Create fresh filesystem directories and helper tools."""
        shutil.rmtree(self.ws_dir, ignore_errors=True)
        self.proj_target.mkdir(parents=True, exist_ok=True)
        self.proj_decoy.mkdir(parents=True, exist_ok=True)
        self.outside_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir.mkdir(parents=True, exist_ok=True)

        self.sleeper_tool, self.forker_tool = compile_helper_tools(self.bin_dir)

        # Prepare source files
        (self.proj_target / "main.c").write_text("int main(){return 0;}\n", encoding="utf-8")
        (self.proj_decoy / "main.c").write_text("int main(){return 0;}\n", encoding="utf-8")
        (self.outside_dir / "main.c").write_text("int main(){return 0;}\n", encoding="utf-8")

        # Initialize SQLite database with projects and initial build samples for estimation
        from precompile.store import DataStore
        with DataStore(db_path=self.db_path) as store:
            store.add_project("proj_target", str(self.proj_target), "custom:gcc")
            store.add_project("proj_decoy", str(self.proj_decoy), "custom:gcc")
            t_now = time.time()
            store.record_build("proj_target", "observed", 0.0, 0.5, t_now - 1.0, t_now)
            store.record_build("proj_decoy", "observed", 0.0, 0.5, t_now - 1.0, t_now)

    def start_daemon(self, condition: str) -> subprocess.Popen:
        """Start the real precompile daemon subprocess."""
        if self.pid_file.exists():
            self.pid_file.unlink(missing_ok=True)

        cmd = [
            sys.executable, "-m", "precompile.cli", "daemon",
            "--actuate",
            "--weight", "500",
            "--db", str(self.db_path),
            "--pid-file", str(self.pid_file),
            "--ablation-no-confidence-gate",
            "--ablation-no-contention-gate",
            "--no-cost-gate",
            "--ablation-fixed-window", "0.0,5.0",
            "--ablation-no-holdout",
            "--leak-check",
            "--log-level", "INFO",
        ]
        if condition == "OFF":
            cmd.append("--ablation-no-atomic-claim")

        log_fp = open(self.log_file, "w", encoding="utf-8")
        proc = subprocess.Popen(cmd, stdout=log_fp, stderr=subprocess.STDOUT, text=True)

        # Wait for PID file
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.pid_file.exists():
                try:
                    pid = int(self.pid_file.read_text().strip())
                    if pid > 0:
                        break
                except Exception:
                    pass
            time.sleep(0.05)
        else:
            raise RuntimeError(f"Daemon failed to start within 5s. Check log: {self.log_file}")

        return proc

    def stop_daemon(self, proc: subprocess.Popen) -> None:
        """Stop daemon cleanly and ensure zero leftover cgroups."""
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=4.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)

        # Cleanup any remaining groups
        cleanup_cmd = [sys.executable, "-m", "precompile.cli", "cleanup", "--db", str(self.db_path)]
        subprocess.run(cleanup_cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.1)

    def run_trial(
        self,
        trial_id: int,
        variant: str,
        N: int,
        condition: str,
    ) -> Dict[str, Any]:
        """Execute a single concurrent claiming trial with barrier launch."""
        # 1. Deterministic arming:
        # Prepare real tool binary named gcc for this trial: copy sleeper or forker
        gcc_bin = self.bin_dir / "gcc"
        if gcc_bin.exists() or gcc_bin.is_symlink():
            gcc_bin.unlink()
        target_tool = self.forker_tool if variant == "V4" else self.sleeper_tool
        shutil.copyfile(str(target_tool), str(gcc_bin))
        os.chmod(str(gcc_bin), 0o755)

        # Trigger save on proj_target
        (self.proj_target / "main.c").write_text(f"// trial {trial_id} {time.time()}\nint main(){{return 0;}}\n")

        target_res_id: Optional[str] = None
        target_grp_dir: Optional[Path] = None
        decoy_res_id: Optional[str] = None
        decoy_grp_dir: Optional[Path] = None

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            active_groups = [
                p for p in Path("/sys/fs/cgroup").iterdir()
                if p.is_dir() and p.name.startswith("precompile-res-")
            ]
            if active_groups:
                target_grp_dir = active_groups[0]
                target_res_id = target_grp_dir.name[len("precompile-res-"):]
                break
            time.sleep(0.01)

        if variant == "V2":
            (self.proj_decoy / "main.c").write_text(f"// trial {trial_id} {time.time()}\nint main(){{return 0;}}\n")
            deadline_v2 = time.monotonic() + 3.0
            while time.monotonic() < deadline_v2:
                active_groups = [
                    p for p in Path("/sys/fs/cgroup").iterdir()
                    if p.is_dir() and p.name.startswith("precompile-res-") and p != target_grp_dir
                ]
                if active_groups:
                    decoy_grp_dir = active_groups[0]
                    decoy_res_id = decoy_grp_dir.name[len("precompile-res-"):]
                    break
                time.sleep(0.01)

        if target_res_id is None or target_grp_dir is None:
            raise RuntimeError(f"Trial {trial_id}: Reservation for target was not armed within 3.0s")
        if variant == "V2" and (decoy_res_id is None or decoy_grp_dir is None):
            raise RuntimeError(f"Trial {trial_id}: Reservation for decoy was not armed within 3.0s")

        # 2. Shared-pipe barrier launcher:
        # Create pipe for release barrier
        pipe_r, pipe_w = os.pipe()

        target_pids: List[int] = []
        decoy_pids: List[int] = []

        # Candidate duration in seconds
        cand_dur_s = 0.25

        # Fork target candidates
        for _ in range(N):
            pid = os.fork()
            if pid == 0:
                # Child
                os.close(pipe_w)
                os.read(pipe_r, 1)  # Block until parent closes pipe_w
                os.close(pipe_r)
                os.chdir(str(self.proj_target))
                os.execv(str(gcc_bin), ["gcc", str(cand_dur_s)])
            else:
                target_pids.append(pid)

        # Fork decoy candidates if V2 or V3
        if variant == "V2":
            for _ in range(N):
                pid = os.fork()
                if pid == 0:
                    os.close(pipe_w)
                    os.read(pipe_r, 1)
                    os.close(pipe_r)
                    os.chdir(str(self.proj_decoy))
                    os.execv(str(gcc_bin), ["gcc", str(cand_dur_s)])
                else:
                    decoy_pids.append(pid)
        elif variant == "V3":
            for _ in range(N):
                pid = os.fork()
                if pid == 0:
                    os.close(pipe_w)
                    os.read(pipe_r, 1)
                    os.close(pipe_r)
                    os.chdir(str(self.outside_dir))
                    os.execv(str(gcc_bin), ["gcc", str(cand_dur_s)])
                else:
                    decoy_pids.append(pid)

        # Close read end in parent, release barrier by closing write end
        os.close(pipe_r)
        t_release = time.monotonic()
        os.close(pipe_w)  # Releases all N (or 2N) candidates simultaneously

        # 3. Live cgroup sampling while candidates are running
        # Sleep 0.08s for watcher scan and daemon claim
        time.sleep(0.08)

        procs_file = target_grp_dir / "cgroup.procs"
        group_procs: List[int] = []
        if procs_file.exists():
            try:
                raw_procs = procs_file.read_text(encoding="utf-8").strip()
                group_procs = [int(p) for p in raw_procs.split() if p.isdigit()]
            except OSError:
                pass

        # Identify which candidates were placed into the target group
        claimant_pids = [p for p in target_pids if p in group_procs]

        # For V4, inspect live descendants of the claimant
        leak_count = 0
        live_descendants: Set[int] = set()
        if variant == "V4" and claimant_pids:
            primary_claimant = claimant_pids[0]
            # Sweep proc for live descendants of claimant
            live_descendants = get_all_descendants_proc(primary_claimant)
            # Live descendants that are not in group_procs are leaks
            leaks = [d for d in live_descendants if d not in group_procs]
            leak_count = len(leaks)

        # Contamination check: any process in group_procs not from target_pids or its live descendants
        valid_pids = set(target_pids).union(live_descendants)
        contamination = [p for p in group_procs if p not in valid_pids]
        contamination_count = len(contamination)

        # 4. Wait for all child processes to exit
        all_children = target_pids + decoy_pids
        for cpid in all_children:
            try:
                os.waitpid(cpid, 0)
            except ChildProcessError:
                pass

        # 5. Wait for daemon to finalize reservation cleanup and close group
        db_claimed_pid: Optional[int] = None
        db_final_state: str = "PENDING"
        db_claimed_at: Optional[float] = None
        claim_lat_s: Optional[float] = None
        group_dir_removed = False

        close_deadline = time.monotonic() + 3.0
        while time.monotonic() < close_deadline:
            with sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT final_state, claimed_pid, claimed_at, claim_latency_s FROM reservations WHERE id = ?",
                    (target_res_id,),
                )
                row = cur.fetchone()
                if row:
                    db_final_state, db_claimed_pid, db_claimed_at, claim_lat_s = row
                    if db_final_state in ("CLOSED", "EXPIRED", "CANCELLED"):
                        break
            time.sleep(0.02)

        # Check group directory removal and orphan cgroups
        group_dir_removed = not target_grp_dir.exists()
        leftovers = [
            p.name for p in Path("/sys/fs/cgroup").iterdir()
            if p.is_dir() and p.name.startswith("precompile-res-")
        ]
        orphan_count = len(leftovers)

        # Secondary timing metric: time from barrier release to claim
        if db_claimed_at is not None and db_claimed_at >= t_release:
            creation_to_claim_ms = (db_claimed_at - t_release) * 1000.0
        elif claim_lat_s is not None and claim_lat_s >= 0:
            creation_to_claim_ms = claim_lat_s * 1000.0
        else:
            creation_to_claim_ms = 0.0

        # Criteria Evaluation:
        # P1: Exactly one candidate is claimant (group procs and DB record agree)
        p1_pass = (
            len(claimant_pids) == 1
            and db_claimed_pid is not None
            and db_claimed_pid == claimant_pids[0]
            and db_final_state == "CLOSED"
        )

        # P2: No process of another project or outside any project is in group
        p2_pass = (contamination_count == 0)

        # P3: In V4, every live descendant of claimant is in group (leaks = 0)
        p3_pass = (leak_count == 0)

        # P4: Reservation reaches CLOSED, group dir removed, no orphan cgroups remain
        p4_pass = (
            db_final_state == "CLOSED"
            and group_dir_removed
            and orphan_count == 0
        )

        trial_pass = p1_pass and p2_pass and p3_pass and p4_pass
        duplicate_claim = len(claimant_pids) > 1

        record = {
            "trial_id": trial_id,
            "plan_commit": self.plan_commit,
            "git_dirty": self.git_meta["git_dirty"],
            "variant": variant,
            "N": N,
            "condition": condition,
            "candidate_pids": target_pids,
            "decoy_pids": decoy_pids,
            "group_members": group_procs,
            "claimant_pids": claimant_pids,
            "db_claimed_pid": db_claimed_pid,
            "db_final_state": db_final_state,
            "leak_count": leak_count,
            "contamination_count": contamination_count,
            "cleanup_status": "CLEAN" if (group_dir_removed and orphan_count == 0) else "DIRTY",
            "orphan_cgroups_count": orphan_count,
            "t_release": t_release,
            "t_claim": db_claimed_at,
            "creation_to_claim_ms": round(creation_to_claim_ms, 3),
            "p1_pass": p1_pass,
            "p2_pass": p2_pass,
            "p3_pass": p3_pass,
            "p4_pass": p4_pass,
            "trial_pass": trial_pass,
            "duplicate_claim": duplicate_claim,
        }

        # Settle post-build suppression tail (+0.3s) before next trial
        time.sleep(0.35)

        return record

    def run_all(self) -> None:
        """Run all variants V1-V4 x N in {2, 8, 32} x {ON, OFF}."""
        self.setup_workspace()

        variants = ["V1", "V2", "V3", "V4"]
        n_values = [2, 8, 32]
        conditions = ["ON", "OFF"]

        total_cells = len(variants) * len(n_values) * len(conditions)
        print("================================================================================")
        print("PreCompile Step 11f: Concurrent Process Claiming Validation")
        print(f"Plan Commit: {self.plan_commit} | Working Tree Clean: {not self.git_meta['git_dirty']}")
        print(f"Total Blocks: {total_cells} | Trials per Block: {self.trials_per_cell} | Total Trials: {total_cells * self.trials_per_cell}")
        print("================================================================================")

        trial_counter = 0

        # Open JSONL for append
        with open(self.output_path, "w", encoding="utf-8") as jsonl_fp:
            for cond in conditions:
                for variant in variants:
                    for N in n_values:
                        print(f"\n>>> Running Block: Variant={variant} | N={N:<2} | Condition={cond:<3} ({self.trials_per_cell} trials) ...")
                        daemon_proc = self.start_daemon(cond)
                        time.sleep(0.2)

                        block_pass = 0
                        block_dup = 0

                        try:
                            for t in range(self.trials_per_cell):
                                trial_counter += 1
                                rec = self.run_trial(trial_counter, variant, N, cond)
                                jsonl_fp.write(json.dumps(rec) + "\n")
                                jsonl_fp.flush()

                                if rec["trial_pass"]:
                                    block_pass += 1
                                if rec["duplicate_claim"]:
                                    block_dup += 1

                                if (t + 1) % 20 == 0 or (t + 1) == self.trials_per_cell:
                                    print(f"  Trial {t+1:>3}/{self.trials_per_cell}: P1-P4 pass rate = {block_pass}/{t+1} | Duplicates = {block_dup}")

                        finally:
                            self.stop_daemon(daemon_proc)

        print("\n================================================================================")
        print(f"Step 11f Experiment Completed: {trial_counter} total trials saved to {self.output_path}")
        print("================================================================================")


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 11f Concurrent Process Claiming Launcher.")
    parser.add_argument("--trials", type=int, default=100, help="Number of trials per cell (default: 100).")
    parser.add_argument("--quick", action="store_true", help="Quick smoke test (2 trials per cell).")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/results_step11f/concurrent_claiming.jsonl"),
        help="Output raw JSONL path.",
    )
    args = parser.parse_args()

    # Enforce root
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("Error: Step 11f experiments must be executed as root (wsl -u root).", file=sys.stderr)
        sys.exit(1)

    runner = ConcurrencyExperimentRunner(
        trials_per_cell=args.trials,
        output_path=args.output,
        quick_smoke=args.quick,
    )
    runner.run_all()


if __name__ == "__main__":
    main()
