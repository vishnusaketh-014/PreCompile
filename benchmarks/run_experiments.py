#!/usr/bin/env bash
"""PreCompile Benchmark Evaluation Harness.

Stage Mapping: Step 11a / Step 11b Part C.
Orchestrates balanced block-rotated experiment runs across conditions, workloads, and
load levels as pre-registered in benchmarks/EXPERIMENT_PLAN.md.
"""

import argparse
import atexit
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
RESULTS_DIR = SCRIPT_DIR / "results"

DEFAULT_CPUSET = "0-1"
DEFAULT_WARMUPS = 12
DEFAULT_CYCLES = 5
DEFAULT_BLOCK_SIZE = 10
PILOT_WARMUPS = 2
PILOT_CYCLES = 1
PILOT_BLOCK_SIZE = 3


def run_cmd(cmd: List[str], check: bool = True, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, timeout=timeout, capture_output=True, text=True)


def check_leftover_cgroups() -> List[str]:
    cgroup_root = Path("/sys/fs/cgroup")
    return [p.name for p in cgroup_root.glob("precompile-res-*") if p.is_dir()]


def cleanup_system(load_script: Path) -> None:
    try:
        subprocess.run([str(load_script), "stop"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        subprocess.run(
            [sys.executable, "-m", "precompile.cli", "cleanup"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def generate_balanced_blocks(conditions: List[str], cycles: int) -> List[Tuple[int, str]]:
    """Generate balanced Latin-square rotated block sequence across cycles.

    Returns list of (cycle_index, condition).
    """
    blocks: List[Tuple[int, str]] = []
    n = len(conditions)
    for c in range(cycles):
        shift = c % n
        rotated = conditions[shift:] + conditions[:shift]
        for cond in rotated:
            blocks.append((c + 1, cond))
    return blocks


class ExperimentRunner:
    def __init__(
        self,
        tier: str = "1",
        pilot: bool = False,
        dry_run: bool = False,
        resume: bool = False,
        cpuset: str = DEFAULT_CPUSET,
        output_dir: Path = RESULTS_DIR,
        project_dir: Optional[Path] = None,
    ) -> None:
        self.tier = tier
        self.pilot = pilot
        self.dry_run = dry_run
        self.resume = resume
        self.cpuset = cpuset
        self.output_dir = Path(output_dir)
        self.project_dir = project_dir or Path("/home/saketh/precompile-demo-heavy")
        self.load_script = SCRIPT_DIR / "load.sh"

        self.cycles = PILOT_CYCLES if pilot else DEFAULT_CYCLES
        self.block_size = PILOT_BLOCK_SIZE if pilot else DEFAULT_BLOCK_SIZE
        self.warmup_builds = PILOT_WARMUPS if pilot else DEFAULT_WARMUPS

        self.jsonl_path = self.output_dir / "results.jsonl"
        self.completed_blocks: Set[Tuple[str, str, str, int]] = set()  # (scenario, load, cond, cycle)

    def init_environment(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not self.dry_run:
            if os.geteuid() != 0:
                print("Error: Experiment execution requires root privileges for cgroup manipulation.", file=sys.stderr)
                sys.exit(1)
            if not self.project_dir.exists():
                print(f"Error: Project directory {self.project_dir} does not exist.", file=sys.stderr)
                sys.exit(1)

        # Register cleanup handler
        if not self.dry_run:
            atexit.register(cleanup_system, self.load_script)

        # Parse existing JSONL if resume is requested
        if self.resume and self.jsonl_path.exists():
            print(f"Checking existing runs in {self.jsonl_path} for resume...")
            with open(self.jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if not rec.get("is_warmup", False):
                            sc = rec.get("scenario")
                            ld = rec.get("load_level")
                            cn = rec.get("condition")
                            cy = rec.get("cycle")
                            if sc and ld and cn and cy:
                                self.completed_blocks.add((sc, ld, cn, int(cy)))
                    except Exception:
                        pass
            print(f"Found {len(self.completed_blocks)} previously completed blocks.")

    def get_plan(self) -> List[Dict[str, Any]]:
        """Construct planned execution matrix according to Tier and EXPERIMENT_PLAN.md."""
        scenarios: List[str] = []
        if self.tier in ("1", "all"):
            scenarios.append("A")
        if self.tier in ("2", "all"):
            scenarios.append("B")

        plan: List[Dict[str, Any]] = []

        for sc in scenarios:
            if self.pilot:
                loads = ["moderate", "high"]
                conds = ["c0-baseline", "c1-reactive", "c2-full", "c3-static"]
            elif self.tier == "1":
                loads = ["idle", "moderate", "high"]
                conds = ["c0-baseline", "c1-reactive", "c2-full", "c3-static"]
            elif self.tier == "2":
                loads = ["high"]
                conds = ["a1-reactive", "a2-noconf", "a3-nocont", "a4-fixedwin", "a5-noholdout"]
            else:
                loads = ["idle", "moderate", "high"]
                conds = ["c0-baseline", "c1-reactive", "c2-full", "c3-static"]

            for ld in loads:
                block_seq = generate_balanced_blocks(conds, self.cycles)
                for cy, cond in block_seq:
                    plan.append({
                        "tier": self.tier,
                        "scenario": sc,
                        "load_level": ld,
                        "condition": cond,
                        "cycle": cy,
                        "builds_per_block": self.block_size,
                        "warmups": self.warmup_builds,
                    })
        return plan

    def _start_load(self, load_level: str) -> None:
        if load_level == "idle":
            run_cmd([str(self.load_script), "stop"], check=False)
            time.sleep(1)
        else:
            env = os.environ.copy()
            env["CPUSET"] = self.cpuset
            subprocess.run([str(self.load_script), load_level], env=env, check=True)
            print(f"  Waiting 12s for PSI cpu stabilization under {load_level} load...")
            time.sleep(12)

    def _stop_load(self) -> None:
        run_cmd([str(self.load_script), "stop"], check=False)
        time.sleep(2)

    def _start_daemon(self, condition: str, db_path: Path, pid_file: Path, log_file: Path) -> Optional[subprocess.Popen]:
        if condition == "c0-baseline":
            # Baseline runs observation-only daemon to track builds without cgroup actuation
            cmd = [
                sys.executable, "-m", "precompile.cli", "daemon",
                "--observe-only",
                "--db", str(db_path),
                "--pid-file", str(pid_file),
                "--log-level", "INFO",
            ]
        else:
            cmd = [
                sys.executable, "-m", "precompile.cli", "daemon",
                "--actuate",
                "--weight", "500",
                "--db", str(db_path),
                "--pid-file", str(pid_file),
                "--log-level", "INFO",
            ]
            if condition == "c1-reactive" or condition == "a1-reactive":
                cmd.extend(["--condition", "c1-reactive"])
            elif condition == "c2-full":
                cmd.extend(["--condition", "c2-full"])
            elif condition == "c3-static":
                cmd.extend(["--condition", "c3-static"])
            elif condition == "a2-noconf":
                cmd.extend(["--ablation-no-confidence-gate"])
            elif condition == "a3-nocont":
                cmd.extend(["--ablation-no-contention-gate"])
            elif condition == "a4-fixedwin":
                cmd.extend(["--ablation-fixed-window", "0.0,5.0"])
            elif condition == "a5-noholdout":
                cmd.extend(["--ablation-no-holdout"])

        log_fp = open(log_file, "w")
        proc = subprocess.Popen(cmd, stdout=log_fp, stderr=subprocess.STDOUT, text=True)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if pid_file.exists():
                break
            time.sleep(0.05)
        return proc

    def _stop_daemon(self, proc: Optional[subprocess.Popen]) -> None:
        if proc is None:
            return
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)

    def _seed_warm_model(self, db_path: Path) -> None:
        """Seed initial observations so confidence & cost gates are active from start."""
        from precompile.store import DataStore
        with DataStore(db_path=db_path) as store:
            for i in range(6):
                t_s = time.time() - (6 - i) * 10
                store.record_sample("heavy", "source", "gcc,make", 1.8, accepted=True, t_save_wall=t_s)
                pid = store.record_prediction("heavy", "source", "gcc,make", t_s, 1.4, 2.2, armed=True, holdout=False)
                store.score_prediction(pid, outcome="hit", observed_delay_s=1.8)
                store.record_build("heavy", "observed", 0.0, 0.5, t_s, t_s + 0.5, prediction_id=pid)
            store.save_model_state(("heavy", "source", "gcc,make"), 6, 1.80, 0.01)

    def run(self) -> None:
        self.init_environment()
        plan = self.get_plan()

        print("================================================================================")
        print(f"PreCompile Experiment Harness - Tier: {self.tier} (Pilot: {self.pilot}, DryRun: {self.dry_run})")
        print(f"Total Blocks Planned: {len(plan)} | Block Size: {self.block_size} builds")
        print("================================================================================")

        if self.dry_run:
            print("\n[DRY RUN] Planned Execution Sequence:")
            for i, p in enumerate(plan, 1):
                key = (p["scenario"], p["load_level"], p["condition"], p["cycle"])
                status = "COMPLETED" if key in self.completed_blocks else "PENDING"
                print(
                    f"  Block {i:>2}: Scenario {p['scenario']} | Load: {p['load_level']:<8} | "
                    f"Cond: {p['condition']:<12} | Cycle {p['cycle']} | Status: {status}"
                )
            print("\n[DRY RUN] Completed without executing benchmarks.")
            return

        current_load: Optional[str] = None
        jsonl_fp = open(self.jsonl_path, "a", encoding="utf-8")

        try:
            for b_idx, block in enumerate(plan, 1):
                sc = block["scenario"]
                ld = block["load_level"]
                cn = block["condition"]
                cy = block["cycle"]
                key = (sc, ld, cn, cy)

                if key in self.completed_blocks:
                    print(f"Skipping completed block {b_idx}/{len(plan)}: {key}")
                    continue

                print(f"\n>>> Starting Block {b_idx}/{len(plan)}: Scenario={sc}, Load={ld}, Cond={cn}, Cycle={cy}")

                # 1. Load transition
                if ld != current_load:
                    print(f"Switching load from {current_load} -> {ld}")
                    self._start_load(ld)
                    current_load = ld

                # 2. Database & Daemon setup
                block_tag = f"{sc}_{ld}_{cn}_c{cy}"
                db_path = self.output_dir / f"db_{block_tag}.db"
                pid_file = self.output_dir / f"pid_{block_tag}.pid"
                log_file = self.output_dir / f"log_{block_tag}.log"

                for f in [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm"), pid_file, log_file]:
                    if f.exists():
                        f.unlink()

                # Clean and pre-compile heavy project
                run_cmd(["make", "-C", str(self.project_dir), "clean"], check=False)
                run_cmd(["make", "-C", str(self.project_dir)], check=True)

                # Register project
                run_cmd([
                    sys.executable, "-m", "precompile.cli", "projects", "add",
                    "--id", "heavy",
                    "--path", str(self.project_dir),
                    "--tools", "gcc,make",
                    "--db", str(db_path),
                ], check=True)

                if cn in ("c2-full", "a2-noconf", "a3-nocont", "a4-fixedwin", "a5-noholdout"):
                    self._seed_warm_model(db_path)

                # 3. Start daemon
                daemon_proc = self._start_daemon(cn, db_path, pid_file, log_file)

                try:
                    # 4. Warm-ups if first cycle for this condition
                    if cy == 1 and self.warmup_builds > 0:
                        print(f"  Running {self.warmup_builds} warm-up builds...")
                        sim_cmd = [
                            sys.executable, "benchmarks/build_on_save.py",
                            "--project", str(self.project_dir),
                            "--cycles", str(self.warmup_builds),
                            "--edit-file", "big.c",
                            "--delay-mean", "1.8",
                            "--delay-jitter", "0.05",
                            "--mode", "auto",
                            "--cpuset", self.cpuset,
                        ]
                        subprocess.run(sim_cmd, timeout=180, check=True, capture_output=True)

                    # 5. Execute measured builds
                    print(f"  Running {self.block_size} measured builds...")
                    for b_num in range(1, self.block_size + 1):
                        t_build_start = time.time()
                        # Run single build-on-save cycle with 60s hard watchdog
                        sim_cmd = [
                            sys.executable, "benchmarks/build_on_save.py",
                            "--project", str(self.project_dir),
                            "--cycles", "1",
                            "--edit-file", "big.c",
                            "--delay-mean", "1.8",
                            "--delay-jitter", "0.05",
                            "--mode", "auto",
                            "--cpuset", self.cpuset,
                        ]
                        t0 = time.monotonic()
                        res = subprocess.run(sim_cmd, timeout=60.0, capture_output=True, text=True)
                        dur = time.monotonic() - t0

                        # Query latest build and reservation row from DB
                        res_rec: Dict[str, Any] = {}
                        import sqlite3
                        with sqlite3.connect(db_path) as conn:
                            conn.row_factory = sqlite3.Row
                            cur = conn.cursor()
                            cur.execute("SELECT * FROM builds ORDER BY id DESC LIMIT 1")
                            b_row = cur.fetchone()
                            cur.execute("SELECT * FROM reservations ORDER BY rowid DESC LIMIT 1")
                            r_row = cur.fetchone()

                        b_dict = dict(b_row) if b_row else {}
                        r_dict = dict(r_row) if r_row else {}

                        dur_wall = b_dict.get("duration_s", dur)
                        lat_s = r_dict.get("claim_latency_s")
                        usage_usec = r_dict.get("cgroup_usage_usec")
                        cgroup_share = (usage_usec / (1e6 * dur_wall)) if (usage_usec is not None and dur_wall > 0) else None

                        record = {
                            "timestamp": time.time(),
                            "tier": self.tier,
                            "scenario": sc,
                            "load_level": ld,
                            "condition": cn,
                            "cycle": cy,
                            "block_index": b_idx,
                            "build_index": b_num,
                            "is_warmup": False,
                            "duration_wall_s": dur_wall,
                            "contention_level": b_dict.get("contention_level", 0.0),
                            "decision": b_dict.get("condition", "unknown"),
                            "res_id": r_dict.get("id"),
                            "claimed": bool(r_dict.get("claimed_pid")),
                            "claim_latency_ms": (lat_s * 1000.0) if lat_s is not None else None,
                            "cgroup_cpu_share": cgroup_share,
                            "exit_code": res.returncode,
                        }
                        jsonl_fp.write(json.dumps(record) + "\n")
                        jsonl_fp.flush()

                finally:
                    # Cleanly terminate daemon
                    self._stop_daemon(daemon_proc)

                # Check for lingering cgroups
                leftovers = check_leftover_cgroups()
                if leftovers:
                    print(f"WARNING: Leftover cgroups found after block {b_idx}: {leftovers}", file=sys.stderr)
                    run_cmd([sys.executable, "-m", "precompile.cli", "cleanup"], check=False)

                self.completed_blocks.add(key)

        finally:
            jsonl_fp.close()
            self._stop_load()
            cleanup_system(self.load_script)

        print("\n================================================================================")
        print(f"Experiments Completed Successfully. Results flushed to: {self.jsonl_path}")
        print("================================================================================")


def main():
    parser = argparse.ArgumentParser(description="PreCompile Experiment Runner")
    parser.add_argument("--tier", choices=["1", "2", "all"], default="1", help="Evaluation tier to execute")
    parser.add_argument("--pilot", action="store_true", default=False, help="Run pilot validation run")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Print plan without running")
    parser.add_argument("--resume", action="store_true", default=False, help="Resume previously interrupted run")
    parser.add_argument("--cpuset", default=DEFAULT_CPUSET, help="CPU affinity set (default: 0-1)")
    parser.add_argument("--output-dir", default=str(RESULTS_DIR), help="Results output directory")
    parser.add_argument("--project-dir", default=None, help="Heavy project directory path")
    args = parser.parse_args()

    proj_dir = Path(args.project_dir) if args.project_dir else None
    runner = ExperimentRunner(
        tier=args.tier,
        pilot=args.pilot,
        dry_run=args.dry_run,
        resume=args.resume,
        cpuset=args.cpuset,
        output_dir=Path(args.output_dir),
        project_dir=proj_dir,
    )
    runner.run()


if __name__ == "__main__":
    main()
