"""Developer workload simulator triggering builds upon file saves.

Reused by Step 6 demonstration and Step 11 experimental benchmarks.
Simulates realistic developer workflows with atomic saves, configurable delay distributions,
and trigger modes matching the 3 specification scenarios:
    - auto: Consistent delay between save and make (Scenario A)
    - manual-irregular: Highly variable delay (Scenario B)
    - save-only: Saves with no following build (Scenario C)
"""

import argparse
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

try:
    import resource
except ImportError:
    resource = None


def atomic_save(file_path: Path, new_content: str) -> None:
    """Perform atomic save using tempfile-plus-rename pattern."""
    target_dir = file_path.parent
    with tempfile.NamedTemporaryFile("w", dir=target_dir, delete=False, encoding="utf-8") as tf:
        tf.write(new_content)
        temp_name = tf.name

    os.replace(temp_name, file_path)


def run_developer_simulation(
    project_path: Path,
    cycles: int = 14,
    delay_mean: float = 1.8,
    delay_jitter: float = 0.1,
    gap: float = 2.0,
    seed: int = 42,
    mode: str = "auto",
    cpuset: Optional[str] = None,
    edit_file: Optional[str] = None,
    log_jsonl: Optional[Path] = None,
) -> List[float]:
    """Simulate N developer save-to-build cycles. Returns list of build durations in seconds."""
    import json
    durations: List[float] = []
    rng = random.Random(seed)

    if edit_file:
        target_file = Path(edit_file)
        if not target_file.is_absolute():
            target_file = project_path / target_file
    else:
        target_file = project_path / "compute.c"
        if not target_file.exists():
            target_file = project_path / "main.c"
        if not target_file.exists():
            target_file = project_path / "big.c"
    if not target_file.exists():
        raise FileNotFoundError(f"No C source file found in project directory '{project_path}'")

    print(f"Starting developer simulation in '{project_path}' (mode={mode}, cycles={cycles}, target={target_file.name})...")

    # Read base file content, stripping any previous marker comments to prevent file growth
    clean_lines = [
        l for l in target_file.read_text(encoding="utf-8").splitlines()
        if not l.startswith("/* build_on_save")
    ]
    base_text = "\n".join(clean_lines).rstrip()

    jsonl_f = None
    if log_jsonl is not None:
        log_jsonl.parent.mkdir(parents=True, exist_ok=True)
        jsonl_f = open(log_jsonl, "a", encoding="utf-8")

    try:
        for i in range(1, cycles + 1):
            # 1. Edit source file via atomic temp-file-plus-rename (non-growing toggle)
            marker_toggle = i % 2
            modified_content = f"{base_text}\n/* build_on_save_marker: {marker_toggle} */\n"
            atomic_save(target_file, modified_content)
            t_save = time.time()
            print(f"Cycle {i:02d}/{cycles:02d}: Saved {target_file.name} at {t_save:.3f}")

            # 2. Determine delay and execute build if not save-only
            if mode == "save-only":
                print(f"  [save-only] Skipping build for cycle {i}")
            else:
                if mode == "auto":
                    delay = max(0.01, rng.gauss(delay_mean, delay_jitter))
                elif mode == "manual-irregular":
                    delay = rng.uniform(0.5, 20.0)
                else:
                    delay = delay_mean

                print(f"  Waiting {delay:.2f}s before triggering build...")
                time.sleep(delay)

                # Trigger make (pinned to cpuset via taskset if specified)
                t_make_start = time.monotonic()
                t_make_start_wall = time.time()
                ru_start = (
                    resource.getrusage(resource.RUSAGE_CHILDREN).ru_utime + resource.getrusage(resource.RUSAGE_CHILDREN).ru_stime
                    if resource is not None else 0.0
                )
                cmd = ["taskset", "-c", cpuset, "make"] if cpuset else ["make"]
                res = subprocess.run(
                    cmd,
                    cwd=str(project_path),
                    capture_output=True,
                    text=True,
                )
                t_make_end = time.monotonic()
                t_make_end_wall = time.time()
                ru_end = (
                    resource.getrusage(resource.RUSAGE_CHILDREN).ru_utime + resource.getrusage(resource.RUSAGE_CHILDREN).ru_stime
                    if resource is not None else 0.0
                )
                build_wall_s = t_make_end - t_make_start
                durations.append(build_wall_s)

                cpu_s = max(0.0, ru_end - ru_start)
                cpu_share = (cpu_s / build_wall_s) if build_wall_s > 0 else 0.0

                if jsonl_f is not None:
                    record = {
                        "cycle": i,
                        "t_save": t_save,
                        "delay": delay,
                        "t_make_start": t_make_start_wall,
                        "t_make_end": t_make_end_wall,
                        "build_wall_s": build_wall_s,
                        "duration_s": build_wall_s,
                        "cpu_s": cpu_s,
                        "cpu_share": cpu_share,
                        "exit_code": res.returncode,
                    }
                    jsonl_f.write(json.dumps(record) + "\n")
                    jsonl_f.flush()

                if res.returncode != 0:
                    print(f"  [make error] Exit code {res.returncode}:\n{res.stderr}")
                else:
                    print(f"  Build completed in {build_wall_s:.3f}s (build_wall_s={build_wall_s:.6f}, cpu={cpu_s:.2f}s share={cpu_share*100:.1f}%, return code {res.returncode})")

            # 3. Inter-cycle gap
            if i < cycles and gap > 0:
                time.sleep(gap)
    finally:
        if jsonl_f is not None:
            jsonl_f.close()

    print("Developer simulation finished successfully.")
    return durations


def main():
    parser = argparse.ArgumentParser(
        description="Simulate developer file saves and builds.",
    )
    parser.add_argument(
        "--project", "-p",
        required=True,
        help="Path to project directory containing C files and Makefile",
    )
    parser.add_argument(
        "--cycles", "-c",
        type=int,
        default=14,
        help="Number of save-to-build cycles (default: 14)",
    )
    parser.add_argument(
        "--delay-mean",
        type=float,
        default=1.8,
        help="Mean delay in seconds between save and make (default: 1.8)",
    )
    parser.add_argument(
        "--delay-jitter",
        type=float,
        default=0.1,
        help="Gaussian jitter standard deviation for delay (default: 0.1)",
    )
    parser.add_argument(
        "--gap",
        type=float,
        default=2.0,
        help="Gap time in seconds between build completion and next save (default: 2.0)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for repeatable delays (default: 42)",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "manual-irregular", "save-only"],
        default="auto",
        help="Simulation mode: auto (Scenario A), manual-irregular (Scenario B), save-only (Scenario C)",
    )
    parser.add_argument(
        "--cpuset",
        default=None,
        help="CPU set to pin build commands with taskset -c (e.g. '0-1')",
    )
    parser.add_argument(
        "--edit-file",
        default=None,
        help="Specific file within project to touch/edit on each cycle",
    )
    parser.add_argument(
        "--log-jsonl",
        default=None,
        help="Path to write JSON Lines execution log for each build cycle",
    )

    args = parser.parse_args()
    proj_path = Path(args.project).resolve()
    if not proj_path.is_dir():
        print(f"Error: Directory '{proj_path}' does not exist.")
        sys.exit(1)

    log_jsonl_path = Path(args.log_jsonl).resolve() if args.log_jsonl else None

    run_developer_simulation(
        project_path=proj_path,
        cycles=args.cycles,
        delay_mean=args.delay_mean,
        delay_jitter=args.delay_jitter,
        gap=args.gap,
        seed=args.seed,
        mode=args.mode,
        cpuset=args.cpuset,
        edit_file=args.edit_file,
        log_jsonl=log_jsonl_path,
    )


if __name__ == "__main__":
    main()
