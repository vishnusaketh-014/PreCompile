"""Workload generator creating a heavy sample project with configurable compilation time.

Calibrates number of functions in big.c so that an incremental rebuild (-O2)
takes approximately --target-seconds (default: 2.0s).
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def generate_big_c(dest_file: Path, num_funcs: int) -> None:
    """Generate a big.c file with num_funcs arithmetic functions compiled with -O2."""
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_file, "w", encoding="utf-8") as f:
        f.write("/* Auto-generated heavy C workload */\n")
        f.write("#include <stdio.h>\n\n")

        for i in range(num_funcs):
            f.write(f"long func_{i}(long x) {{\n")
            f.write(f"    long acc = x + {i};\n")
            for j in range(25):
                f.write(f"    acc = (acc * 1103515245 + 12345) ^ (acc >> {j % 7 + 1});\n")
            f.write("    return acc;\n")
            f.write("}\n\n")

        f.write("int main(void) {\n")
        f.write("    long total = 0;\n")
        for i in range(num_funcs):
            f.write(f"    total += func_{i}({i});\n")
        f.write('    printf("Total: %ld\\n", total);\n')
        f.write("    return 0;\n")
        f.write("}\n")


def generate_makefile(dest_dir: Path) -> None:
    """Generate a Makefile that compiles big.c with -O2."""
    makefile = dest_dir / "Makefile"
    content = (
        "CC ?= gcc\n"
        "CFLAGS ?= -O2 -Wall\n\n"
        "all: big_prog\n\n"
        "big_prog: big.o\n"
        "\t$(CC) $(CFLAGS) -o big_prog big.o\n\n"
        "big.o: big.c\n"
        "\t$(CC) $(CFLAGS) -c big.c -o big.o\n\n"
        "clean:\n"
        "\trm -f big.o big_prog\n"
    )
    makefile.write_text(content, encoding="utf-8")


def time_rebuild(project_dir: Path) -> float:
    """Touch big.c and measure incremental rebuild time."""
    big_c = project_dir / "big.c"
    # Append comment to trigger rebuild
    with open(big_c, "a", encoding="utf-8") as f:
        f.write(f"/* touch {time.time()} */\n")

    t0 = time.time()
    res = subprocess.run(["make"], cwd=str(project_dir), capture_output=True, text=True)
    dur = time.time() - t0
    if res.returncode != 0:
        raise RuntimeError(f"Make failed: {res.stderr}")
    return dur


def calibrate_workload(target_dir: Path, target_seconds: float = 2.0) -> int:
    """Iteratively calibrate num_funcs until median incremental build duration is ~target_seconds."""
    print(f"Calibrating heavy workload in '{target_dir}' to ~{target_seconds:.1f}s...")
    target_dir.mkdir(parents=True, exist_ok=True)
    generate_makefile(target_dir)

    # Initial guess: 1200 functions
    num_funcs = 1200
    big_c = target_dir / "big.c"

    for iteration in range(6):
        generate_big_c(big_c, num_funcs)
        # Clean build first
        subprocess.run(["make", "clean"], cwd=str(target_dir), capture_output=True)
        subprocess.run(["make"], cwd=str(target_dir), capture_output=True)

        # Measure 3 incremental rebuilds
        times = []
        for _ in range(3):
            t = time_rebuild(target_dir)
            times.append(t)
            time.sleep(0.1)

        median_t = sorted(times)[1]
        print(f"  Iteration {iteration + 1}: num_funcs={num_funcs} -> median rebuild {median_t:.3f}s (runs: {[round(x, 3) for x in times]})")

        if abs(median_t - target_seconds) <= 0.25 * target_seconds or iteration == 5:
            print(f"Calibrated: num_funcs={num_funcs} achieves {median_t:.2f}s build time.")
            return num_funcs

        # Adjust num_funcs proportionally with dampening
        ratio = target_seconds / max(0.1, median_t)
        # Dampen ratio: sqrt(ratio) or clamped
        adj_ratio = max(0.3, min(3.0, ratio))
        num_funcs = max(200, int(num_funcs * adj_ratio))

    return num_funcs


def main():
    parser = argparse.ArgumentParser(description="Generate and calibrate heavy C workload.")
    parser.add_argument(
        "--target-dir",
        default="benchmarks/sample_project_heavy",
        help="Target project directory (default: benchmarks/sample_project_heavy)",
    )
    parser.add_argument(
        "--target-seconds",
        type=float,
        default=2.0,
        help="Target incremental build duration in seconds at idle (default: 2.0)",
    )
    args = parser.parse_args()

    dest = Path(args.target_dir).resolve()
    calibrate_workload(dest, args.target_seconds)


if __name__ == "__main__":
    main()
