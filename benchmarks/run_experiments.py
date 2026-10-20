#!/usr/bin/env python3
"""PreCompile Benchmark Evaluation Harness.

Stage Mapping: Step 11a / Step 11b Part C / Step 11a3.
Orchestrates balanced block-rotated experiment runs across conditions, workloads, and
load levels as pre-registered in benchmarks/EXPERIMENT_PLAN.md (Version 1.1.0).
"""

import argparse
import atexit
import glob
import json
import os
import platform
import re
import signal
import sqlite3
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
PILOT_BLOCK_SIZE = 5


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


def wait_for_ramp_down(target_psi: float = 5.0, max_wait_s: float = 40.0) -> float:
    t0 = time.monotonic()
    current_psi = read_psi_cpu_avg10()
    while current_psi >= target_psi and (time.monotonic() - t0) < max_wait_s:
        time.sleep(1.0)
        current_psi = read_psi_cpu_avg10()
    elapsed = time.monotonic() - t0
    print(f"  [ramp-down] PSI some_avg10 settled to {current_psi:.2f}% in {elapsed:.1f}s (target < {target_psi}%)")
    return current_psi


def get_stress_pids() -> List[int]:
    pids = []
    for p in glob.glob("/proc/[0-9]*"):
        try:
            with open(f"{p}/comm", "r") as f:
                comm = f.read().strip()
                if "stress-ng" in comm:
                    pids.append(int(os.path.basename(p)))
        except Exception:
            pass
    return pids


def get_process_cpu_ticks(pid: int) -> Optional[int]:
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            fields = f.read().split()
            # utime is index 13, stime is index 14
            return int(fields[13]) + int(fields[14])
    except Exception:
        return None


def get_stress_cpu_ticks() -> int:
    total = 0
    for pid in get_stress_pids():
        ticks = get_process_cpu_ticks(pid)
        if ticks is not None:
            total += ticks
    return total


def get_git_metadata() -> Dict[str, Any]:
    commit = "unknown"
    is_dirty = False
    plan_differs = False
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
    try:
        res_plan = subprocess.run(
            ["git", "diff", "HEAD", "--", "benchmarks/EXPERIMENT_PLAN.md"],
            cwd=str(REPO_DIR), capture_output=True, text=True, check=False
        )
        plan_differs = bool(res_plan.stdout.strip())
    except Exception:
        plan_differs = False
    return {
        "git_commit": commit,
        "git_dirty": is_dirty,
        "plan_differs_from_head": plan_differs,
    }


def get_system_metadata(cpuset: str, tier: str, pilot: bool, force: bool = False) -> Dict[str, Any]:
    stress_ver = "unknown"
    try:
        res = subprocess.run(["stress-ng", "--version"], capture_output=True, text=True, check=False)
        m = re.search(r"version\s+([\d\.]+)", res.stdout or res.stderr)
        if m:
            stress_ver = m.group(1)
    except Exception:
        pass

    cpu_model = "unknown"
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line:
                    cpu_model = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass

    meta = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kernel": platform.release(),
        "cpu_model": cpu_model,
        "cpu_count": os.cpu_count(),
        "cpuset": cpuset,
        "stress_ng_version": stress_ver,
        "cgroup_controller": "cgroup v2",
        "tier": tier,
        "pilot": pilot,
        "force": force,
    }
    meta.update(get_git_metadata())
    return meta


def get_daemon_threads_stat(daemon_pid: int) -> Dict[int, Tuple[str, int]]:
    """Returns {tid: (comm, utime + stime)} for all threads of daemon_pid."""
    stats: Dict[int, Tuple[str, int]] = {}
    task_dir = Path(f"/proc/{daemon_pid}/task")
    if not task_dir.exists():
        return stats
    try:
        for tid_path in task_dir.iterdir():
            try:
                tid = int(tid_path.name)
                comm_file = tid_path / "comm"
                stat_file = tid_path / "stat"
                comm = comm_file.read_text(encoding="utf-8", errors="replace").strip() if comm_file.exists() else str(tid)
                if stat_file.exists():
                    fields = stat_file.read_text(encoding="utf-8", errors="replace").split()
                    ticks = int(fields[13]) + int(fields[14])
                    stats[tid] = (comm, ticks)
            except Exception:
                pass
    except Exception:
        pass
    return stats


def generate_balanced_blocks(conditions: List[str], cycles: int) -> List[Tuple[int, str]]:
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
        force: bool = False,
        cpuset: str = DEFAULT_CPUSET,
        output_dir: Path = RESULTS_DIR,
        project_dir: Optional[Path] = None,
    ) -> None:
        self.tier = tier
        self.pilot = pilot
        self.dry_run = dry_run
        self.resume = resume
        self.force = force
        self.cpuset = cpuset
        self.output_dir = Path(output_dir)
        self.project_dir = project_dir or Path("/home/saketh/precompile-demo-heavy")
        self.load_script = SCRIPT_DIR / "load.sh"

        self.cycles = 1 if pilot else DEFAULT_CYCLES
        self.block_size = PILOT_BLOCK_SIZE if pilot else DEFAULT_BLOCK_SIZE
        self.warmup_builds = PILOT_WARMUPS if pilot else DEFAULT_WARMUPS

        self.jsonl_path = self.output_dir / "results.jsonl"
        self.header_path = self.output_dir / "harness_header.json"
        self.block_metrics_path = self.output_dir / "block_metrics.jsonl"
        self.completed_blocks: Set[Tuple[str, str, str, int]] = set()

    def init_environment(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not self.dry_run:
            if os.geteuid() != 0:
                print("Error: Experiment execution requires root privileges for cgroup manipulation.", file=sys.stderr)
                sys.exit(1)
            if not self.project_dir.exists():
                print(f"Error: Project directory {self.project_dir} does not exist.", file=sys.stderr)
                sys.exit(1)

        # Write dynamic metadata header
        meta = get_system_metadata(self.cpuset, self.tier, self.pilot, self.force)
        with open(self.header_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        print(f"Recorded dynamic environment metadata to: {self.header_path}")
        print(f"  Kernel: {meta['kernel']} | CPU: {meta['cpu_model']} | stress-ng: {meta['stress_ng_version']}")
        print(f"  Git commit: {meta.get('git_commit', 'unknown')} | dirty: {meta.get('git_dirty', False)}")

        # Refusal check on dirty tree for non-pilot runs (Item 6)
        if not self.pilot and self.tier in ("1", "2", "all"):
            if (meta.get("git_dirty") or meta.get("plan_differs_from_head")) and not self.force:
                print(
                    f"Error: Refusing to start non-pilot {self.tier} run: working tree dirty={meta.get('git_dirty')} "
                    f"or EXPERIMENT_PLAN.md differs from HEAD ({meta.get('plan_differs_from_head')}). "
                    "Commit all changes or pass --force to override.",
                    file=sys.stderr,
                )
                sys.exit(1)

        if not self.dry_run:
            atexit.register(cleanup_system, self.load_script)

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
        plan: List[Dict[str, Any]] = []

        if self.pilot:
            # Validation Pilot 3 plan as pre-registered in benchmarks/EXPERIMENT_PLAN.md (v1.2):
            # 1. Scenario A at high load for C0, C1, C2, C3 (1 block of 5 builds each, with warm-ups)
            for cond in ["c0-baseline", "c1-reactive", "c2-full", "c3-static"]:
                plan.append({
                    "tier": "pilot",
                    "scenario": "A",
                    "load_level": "high",
                    "condition": cond,
                    "cycle": 1,
                    "builds_per_block": 5,
                    "warmups": 2,
                    "mode": "auto",
                })

            # 2. Scenario B at moderate load for C0 and C2 (1 block of 10 builds, with 12 irregular warm-up builds for C2)
            plan.append({
                "tier": "pilot",
                "scenario": "B",
                "load_level": "moderate",
                "condition": "c0-baseline",
                "cycle": 1,
                "builds_per_block": 10,
                "warmups": 0,
                "mode": "manual-irregular",
            })
            plan.append({
                "tier": "pilot",
                "scenario": "B",
                "load_level": "moderate",
                "condition": "c2-full",
                "cycle": 1,
                "builds_per_block": 10,
                "warmups": 12,
                "mode": "manual-irregular",
            })

            # 3. Scenario C at moderate load for C2 (20 saves)
            plan.append({
                "tier": "pilot",
                "scenario": "C",
                "load_level": "moderate",
                "condition": "c2-full",
                "cycle": 1,
                "builds_per_block": 20,
                "warmups": 0,
                "mode": "save-only",
            })
            return plan

        # Full experiments plan
        if self.tier in ("1", "all"):
            # Scenario A across idle, moderate, high load
            loads = ["idle", "moderate", "high"]
            conds = ["c0-baseline", "c1-reactive", "c2-full", "c3-static"]
            for ld in loads:
                block_seq = generate_balanced_blocks(conds, self.cycles)
                for cy, cond in block_seq:
                    plan.append({
                        "tier": "1",
                        "scenario": "A",
                        "load_level": ld,
                        "condition": cond,
                        "cycle": cy,
                        "builds_per_block": self.block_size,
                        "warmups": self.warmup_builds if cy == 1 else 0,
                        "mode": "auto",
                    })

        if self.tier in ("2", "all"):
            # Scenario B at moderate load: 30 builds per condition (3 blocks of 10)
            conds = ["c0-baseline", "c1-reactive", "c2-full", "c3-static"]
            block_seq = generate_balanced_blocks(conds, 3)
            for cy, cond in block_seq:
                plan.append({
                    "tier": "2",
                    "scenario": "B",
                    "load_level": "moderate",
                    "condition": cond,
                    "cycle": cy,
                    "builds_per_block": 10,
                    "warmups": 0,
                    "mode": "manual-irregular",
                })

            # Scenario C at moderate load: 50 saves per condition (1 block of 50 saves)
            for cond in ["c0-baseline", "c1-reactive", "c2-full", "c3-static"]:
                plan.append({
                    "tier": "2",
                    "scenario": "C",
                    "load_level": "moderate",
                    "condition": cond,
                    "cycle": 1,
                    "builds_per_block": 50,
                    "warmups": 0,
                    "mode": "save-only",
                })

        return plan

    def _start_load(self, load_level: str, previous_load: Optional[str] = None) -> None:
        load_order = {"idle": 0, "moderate": 1, "high": 2}
        is_decrease = (
            previous_load is not None
            and load_order.get(load_level, 0) < load_order.get(previous_load, 0)
        )

        if load_level == "idle":
            run_cmd([str(self.load_script), "stop"], check=False)
            time.sleep(1)
            if is_decrease:
                wait_for_ramp_down(target_psi=5.0, max_wait_s=40.0)
        else:
            env = os.environ.copy()
            env["CPUSET"] = self.cpuset
            subprocess.run([str(self.load_script), load_level], env=env, check=True)
            if is_decrease:
                wait_for_ramp_down(target_psi=5.0, max_wait_s=40.0)
            else:
                print(f"  Waiting 12s for PSI cpu stabilization under {load_level} load...")
                time.sleep(12)

    def _stop_load(self) -> None:
        run_cmd([str(self.load_script), "stop"], check=False)
        time.sleep(2)

    def _start_daemon(self, condition: str, db_path: Path, pid_file: Path, log_file: Path) -> Optional[subprocess.Popen]:
        if condition == "c0-baseline":
            # C0 = No daemon at all (natural uninhibited execution)
            return None

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

        if condition != "c0-baseline":
            cmd.append("--leak-check")

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
                    f"Cond: {p['condition']:<12} | Cycle {p['cycle']} | Mode: {p['mode']} | Status: {status}"
                )
            print("\n[DRY RUN] Completed without executing benchmarks.")
            return

        current_load: Optional[str] = None
        jsonl_fp = open(self.jsonl_path, "a", encoding="utf-8")
        block_fp = open(self.block_metrics_path, "a", encoding="utf-8")

        clk_tck = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

        try:
            for b_idx, block in enumerate(plan, 1):
                sc = block["scenario"]
                ld = block["load_level"]
                cn = block["condition"]
                cy = block["cycle"]
                num_items = block["builds_per_block"]
                warmups = block.get("warmups", 0)
                sim_mode = block.get("mode", "auto")
                key = (sc, ld, cn, cy)

                if key in self.completed_blocks:
                    print(f"Skipping completed block {b_idx}/{len(plan)}: {key}")
                    continue

                print(f"\n>>> Starting Block {b_idx}/{len(plan)}: Scenario={sc}, Load={ld}, Cond={cn}, Cycle={cy}, Mode={sim_mode}")

                # 1. Database & Artifact setup
                # Cell-scoped DB: one database per (scenario, load_level, condition)
                cell_tag = f"{sc}_{ld}_{cn}"
                db_path = self.output_dir / f"db_{cell_tag}.db"
                pid_file = self.output_dir / f"pid_{sc}_{ld}_{cn}_c{cy}.pid"
                log_file = self.output_dir / f"log_{sc}_{ld}_{cn}_c{cy}.log"

                for f in [pid_file, log_file]:
                    if f.exists():
                        f.unlink()

                # If first cycle of this cell, start with empty database (Item 3a)
                is_first_cycle = (cy == 1 and (sc, ld, cn, 1) not in self.completed_blocks)
                if is_first_cycle:
                    for f in [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]:
                        if f.exists():
                            f.unlink()

                # Clean and pre-compile heavy project
                run_cmd(["make", "-C", str(self.project_dir), "clean"], check=False)
                run_cmd(["make", "-C", str(self.project_dir)], check=True)

                # Register project if DB was freshly created
                if is_first_cycle or not db_path.exists():
                    run_cmd([
                        sys.executable, "-m", "precompile.cli", "projects", "add",
                        "--id", "heavy",
                        "--path", str(self.project_dir),
                        "--tools", "gcc,make",
                        "--db", str(db_path),
                    ], check=True)

                # 2. Start daemon BEFORE PSI settling wait (Item 3c)
                daemon_proc = self._start_daemon(cn, db_path, pid_file, log_file)
                daemon_pid = daemon_proc.pid if daemon_proc else None
                daemon_ticks_start = get_process_cpu_ticks(daemon_pid) if daemon_pid else 0
                threads_stat_start = get_daemon_threads_stat(daemon_pid) if daemon_pid else {}

                # 3. Transition load if required
                if ld != current_load:
                    print(f"Switching load from {current_load} -> {ld}")
                    self._start_load(ld, previous_load=current_load)
                    current_load = ld

                # Log PSI at block start
                block_start_psi = read_psi_cpu_avg10()
                print(f"  [block-start] System CPU PSI some_avg10 = {block_start_psi:.2f}% (Load: {ld}, Cond: {cn})")

                # Snapshot stress worker CPU ticks
                stress_ticks_start = get_stress_cpu_ticks()
                t_block_start = time.monotonic()

                try:
                    # 4. Warm-ups (use cell's own distribution; counted separately and excluded from measured results)
                    if warmups > 0:
                        print(f"  Running {warmups} warm-up builds (mode={sim_mode})...")
                        sim_cmd = [
                            sys.executable, str(SCRIPT_DIR / "build_on_save.py"),
                            "--project", str(self.project_dir),
                            "--cycles", str(warmups),
                            "--edit-file", "big.c",
                            "--delay-mean", "1.8",
                            "--delay-jitter", "0.1",
                            "--mode", sim_mode,
                            "--cpuset", self.cpuset,
                        ]
                        subprocess.run(sim_cmd, timeout=300, check=True, capture_output=True)

                    # Snapshot DB row IDs at the start of measured workload (Item 3b)
                    min_pred_id = 0
                    min_res_id = 0
                    min_build_id = 0
                    if db_path.exists():
                        with sqlite3.connect(db_path) as conn:
                            cur = conn.cursor()
                            cur.execute("SELECT coalesce(max(id), 0) FROM predictions")
                            min_pred_id = cur.fetchone()[0]
                            cur.execute("SELECT coalesce(max(rowid), 0) FROM reservations")
                            min_res_id = cur.fetchone()[0]
                            cur.execute("SELECT coalesce(max(id), 0) FROM builds")
                            min_build_id = cur.fetchone()[0]
                    min_pred_id_start = min_pred_id
                    min_res_id_start = min_res_id
                    min_build_id_start = min_build_id

                    # 5. Execute workload
                    if sim_mode == "save-only":
                        print(f"  Running {num_items} save-only iterations...")
                        sim_cmd = [
                            sys.executable, str(SCRIPT_DIR / "build_on_save.py"),
                            "--project", str(self.project_dir),
                            "--cycles", str(num_items),
                            "--edit-file", "big.c",
                            "--gap", "2.0",
                            "--mode", "save-only",
                            "--cpuset", self.cpuset,
                        ]
                        subprocess.run(sim_cmd, timeout=300, check=True, capture_output=True)
                    else:
                        print(f"  Running {num_items} measured builds (mode={sim_mode})...")
                        for b_idx_in_block in range(num_items):
                            build_num = b_idx_in_block + 1
                            sim_cmd = [
                                sys.executable, str(SCRIPT_DIR / "build_on_save.py"),
                                "--project", str(self.project_dir),
                                "--cycles", "1",
                                "--edit-file", "big.c",
                                "--delay-mean", "1.8",
                                "--delay-jitter", "0.1",
                                "--mode", sim_mode,
                                "--cpuset", self.cpuset,
                            ]
                            t_start_build = time.time()
                            t0 = time.monotonic()
                            res = subprocess.run(sim_cmd, timeout=60.0, capture_output=True, text=True)
                            dur_wall_fallback = time.monotonic() - t0

                            # Parse authoritative build_wall_s directly from build_on_save stdout (Item 1)
                            build_wall_s = None
                            m_bwall = re.search(r"build_wall_s=([\d\.]+)", res.stdout)
                            if m_bwall:
                                build_wall_s = float(m_bwall.group(1))
                            else:
                                build_wall_s = dur_wall_fallback

                            # Parse cpu_seconds from stdout of build_on_save
                            cpu_s = 0.0
                            m_cpu = re.search(r"cpu=([\d\.]+)s", res.stdout)
                            if m_cpu:
                                cpu_s = float(m_cpu.group(1))

                            # Query SQLite DB for build and reservation records created during this build
                            b_dict: Dict[str, Any] = {}
                            r_dict: Dict[str, Any] = {}
                            if db_path.exists():
                                with sqlite3.connect(db_path) as conn:
                                    conn.row_factory = sqlite3.Row
                                    cur = conn.cursor()
                                    cur.execute("SELECT * FROM builds WHERE id > ? ORDER BY id DESC LIMIT 1", (min_build_id,))
                                    b_row = cur.fetchone()
                                    if b_row:
                                        b_dict = dict(b_row)
                                        min_build_id = max(min_build_id, b_dict.get("id", min_build_id))
                                    cur.execute("SELECT * FROM reservations WHERE rowid > ? ORDER BY rowid DESC LIMIT 1", (min_res_id,))
                                    r_row = cur.fetchone()
                                    if r_row:
                                        r_dict = dict(r_row)
                                        min_res_id = max(min_res_id, r_dict.get("rowid", min_res_id))

                            daemon_dur = b_dict.get("duration_s")
                            wall_daemon_diff = abs(build_wall_s - daemon_dur) if daemon_dur is not None else None

                            lat_s = r_dict.get("claim_latency_s")
                            usage_usec = r_dict.get("cgroup_usage_usec")
                            cgroup_share = (usage_usec / (1e6 * build_wall_s)) if (usage_usec is not None and build_wall_s > 0) else None

                            # Unboosted vs Abstained detection (Item 4)
                            unboosted = False
                            abstained = False
                            unboosted_reason: Optional[str] = None
                            if cn in ("c1-reactive", "c2-full", "c3-static", "a1-reactive", "a2-noconf", "a3-nocont", "a4-fixedwin", "a5-noholdout"):
                                is_claimed = bool(r_dict.get("claimed_pid"))
                                is_holdout = (b_dict.get("condition") == "holdout")
                                decision = b_dict.get("condition", "unknown")
                                if is_holdout:
                                    unboosted = True
                                    unboosted_reason = "holdout"
                                elif is_claimed:
                                    if cgroup_share is not None and cgroup_share < 0.50:
                                        unboosted = True
                                        unboosted_reason = f"cgroup throttling (share={cgroup_share*100:.1f}%)"
                                    elif lat_s is not None and lat_s > 0.100:
                                        unboosted = True
                                        unboosted_reason = f"late claim ({lat_s*1000:.1f}ms)"
                                else:
                                    # Not claimed: check if boost was intended or if gate correctly abstained
                                    if ld == "idle" or decision in ("unpredicted", "gate_closed", "abstained"):
                                        abstained = True
                                        unboosted = False
                                        unboosted_reason = "not boosted (abstained)"
                                    else:
                                        unboosted = True
                                        unboosted_reason = "no reservation / missed"

                            record = {
                                "timestamp": t_start_build,
                                "tier": block.get("tier", self.tier),
                                "scenario": sc,
                                "load_level": ld,
                                "condition": cn,
                                "cycle": cy,
                                "block_index": b_idx,
                                "block_build_index": b_idx_in_block,  # 0-based
                                "build_index": build_num,             # 1-based
                                "is_warmup": False,
                                "build_wall_s": build_wall_s,
                                "duration_wall_s": build_wall_s,
                                "daemon_duration_s": daemon_dur,
                                "wall_daemon_diff_s": wall_daemon_diff,
                                "cpu_seconds": cpu_s,
                                "contention_level": b_dict.get("contention_level", block_start_psi),
                                "block_start_psi": block_start_psi,
                                "decision": b_dict.get("condition", "observed" if cn == "c0-baseline" else "unknown"),
                                "res_id": r_dict.get("id"),
                                "claimed": bool(r_dict.get("claimed_pid")),
                                "claim_latency_ms": (lat_s * 1000.0) if lat_s is not None else None,
                                "cgroup_cpu_share": cgroup_share,
                                "unboosted": unboosted,
                                "abstained": abstained,
                                "unboosted_reason": unboosted_reason,
                                "exit_code": res.returncode,
                            }
                            jsonl_fp.write(json.dumps(record) + "\n")
                            jsonl_fp.flush()

                finally:
                    t_block_wall = max(0.001, time.monotonic() - t_block_start)
                    daemon_ticks_end = get_process_cpu_ticks(daemon_pid) if daemon_pid else 0
                    threads_stat_end = get_daemon_threads_stat(daemon_pid) if daemon_pid else {}
                    stress_ticks_end = get_stress_cpu_ticks()

                    # Compute daemon CPU %
                    daemon_cpu_pct = 0.0
                    thread_cpu_breakdown: Dict[str, Any] = {}
                    if daemon_pid and daemon_ticks_end >= daemon_ticks_start:
                        d_ticks = daemon_ticks_end - daemon_ticks_start
                        daemon_cpu_pct = (d_ticks / clk_tck / t_block_wall) * 100.0

                        # Per-thread breakdown (Item 2a)
                        all_tids = set(threads_stat_start.keys()) | set(threads_stat_end.keys())
                        for tid in all_tids:
                            comm = threads_stat_end.get(tid, threads_stat_start.get(tid, ("unknown", 0)))[0]
                            t_start = threads_stat_start.get(tid, (comm, 0))[1]
                            t_end = threads_stat_end.get(tid, (comm, 0))[1]
                            dt = max(0, t_end - t_start)
                            pct_c = (dt / clk_tck / t_block_wall) * 100.0 if t_block_wall > 0 else 0.0
                            pct_d = (dt / d_ticks * 100.0) if d_ticks > 0 else 0.0
                            if comm not in thread_cpu_breakdown:
                                thread_cpu_breakdown[comm] = {"ticks": 0, "pct_core": 0.0, "pct_daemon": 0.0}
                            thread_cpu_breakdown[comm]["ticks"] += dt
                            thread_cpu_breakdown[comm]["pct_core"] += pct_c
                            thread_cpu_breakdown[comm]["pct_daemon"] += pct_d

                    # Compute background impact (CPU sec per wall sec)
                    bg_cpu_rate = 0.0
                    if stress_ticks_end >= stress_ticks_start:
                        s_ticks = stress_ticks_end - stress_ticks_start
                        bg_cpu_rate = (s_ticks / clk_tck) / t_block_wall

                    # Stop daemon cleanly
                    self._stop_daemon(daemon_proc)

                # Parse watcher shutdown stats from log
                watcher_stats: Dict[str, Any] = {}
                if log_file.exists():
                    log_text = log_file.read_text(encoding="utf-8", errors="replace")
                    m_stat = re.search(
                        r"\[shutdown\] Watcher stats: scans=(\d+), avg_scan=([\d\.]+)ms, max_scan=([\d\.]+)ms",
                        log_text,
                    )
                    if m_stat:
                        scans = int(m_stat.group(1))
                        avg_s = float(m_stat.group(2))
                        watcher_stats = {
                            "scans_total": scans,
                            "avg_scan_ms": avg_s,
                            "scans_per_sec": scans / t_block_wall,
                        }

                # Compute DB metrics strictly scoped to rows created during measured workload (Item 3b)
                hit_rate: Optional[float] = None
                false_res_rate: Optional[float] = None
                missed_rate: Optional[float] = None
                if db_path.exists():
                    try:
                        with sqlite3.connect(db_path) as conn:
                            cur = conn.cursor()
                            # Predictions scoped to measured workload
                            cur.execute(
                                "SELECT count(*) FROM predictions WHERE id > ? AND outcome = 'hit'",
                                (min_pred_id_start,)
                            )
                            n_hits = cur.fetchone()[0]
                            cur.execute(
                                "SELECT count(*) FROM predictions WHERE id > ? AND outcome IN ('hit', 'miss')",
                                (min_pred_id_start,)
                            )
                            n_scored = cur.fetchone()[0]
                            hit_rate = (n_hits / n_scored) if n_scored > 0 else None

                            # Reservations scoped to measured workload
                            cur.execute(
                                "SELECT count(*) FROM reservations WHERE rowid > ? AND final_state = 'EXPIRED'",
                                (min_res_id_start,)
                            )
                            n_exp = cur.fetchone()[0]
                            cur.execute(
                                "SELECT count(*) FROM reservations WHERE rowid > ?",
                                (min_res_id_start,)
                            )
                            n_res = cur.fetchone()[0]
                            false_res_rate = (n_exp / n_res) if n_res > 0 else None

                            # Builds scoped to measured workload
                            cur.execute(
                                "SELECT count(*) FROM builds WHERE id > ? AND (condition = 'unpredicted' OR prediction_id IS NULL)",
                                (min_build_id_start,)
                            )
                            n_unpred = cur.fetchone()[0]
                            cur.execute(
                                "SELECT count(*) FROM builds WHERE id > ?",
                                (min_build_id_start,)
                            )
                            n_b = cur.fetchone()[0]
                            missed_rate = (n_unpred / n_b) if n_b > 0 else None
                    except Exception as e:
                        print(f"  [db-metrics-warning] Could not compute metrics from {db_path}: {e}", file=sys.stderr)

                block_summary = {
                    "block_index": b_idx,
                    "tier": block.get("tier", self.tier),
                    "scenario": sc,
                    "load_level": ld,
                    "condition": cn,
                    "cycle": cy,
                    "wall_time_s": t_block_wall,
                    "block_start_psi": block_start_psi,
                    "daemon_cpu_pct": daemon_cpu_pct,
                    "thread_cpu_breakdown": thread_cpu_breakdown,
                    "watcher_stats": watcher_stats,
                    "bg_cpu_rate": bg_cpu_rate,
                    "hit_rate": hit_rate,
                    "false_res_rate": false_res_rate,
                    "missed_rate": missed_rate,
                }
                block_fp.write(json.dumps(block_summary) + "\n")
                block_fp.flush()

                th_info = ""
                if thread_cpu_breakdown:
                    th_parts = [f"{comm}:{info['pct_core']:.2f}%" for comm, info in sorted(thread_cpu_breakdown.items())]
                    th_info = f" | Threads=[{', '.join(th_parts)}]"
                print(f"  [block-summary] Wall={t_block_wall:.1f}s | DaemonCPU={daemon_cpu_pct:.2f}%{th_info} | BgRate={bg_cpu_rate:.2f} cpu/s | Scans/s={watcher_stats.get('scans_per_sec', 0.0):.1f}")

                # Check leftover cgroups
                leftovers = check_leftover_cgroups()
                if leftovers:
                    print(f"WARNING: Leftover cgroups found after block {b_idx}: {leftovers}", file=sys.stderr)
                    run_cmd([sys.executable, "-m", "precompile.cli", "cleanup"], check=False)

                self.completed_blocks.add(key)

        finally:
            jsonl_fp.close()
            block_fp.close()
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
    parser.add_argument("--force", action="store_true", default=False, help="Force execution even if working tree is dirty")
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
        force=args.force,
        cpuset=args.cpuset,
        output_dir=Path(args.output_dir),
        project_dir=proj_dir,
    )
    runner.run()


if __name__ == "__main__":
    main()
