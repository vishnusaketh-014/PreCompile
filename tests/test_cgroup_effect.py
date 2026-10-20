"""The Proof Test: Validates real Linux cgroup v2 CFS priority weighting.

Stage Mapping: Stage 6 (Anticipatory Reservation) & Stage 10 (Verification).

Design Rule:
    Reservation groups run as direct children of /sys/fs/cgroup (e.g. precompile-res-<id>)
    with elevated cpu.weight (default 500). They compete directly against ordinary work
    (e.g. init.scope or user.slice) running at default cpu.weight=100 on the same core.
    CFS time allocation ratio between weight 500 and weight 100 on a single saturated CPU is ~5:1.

Requirements:
    - Skips unless run with root privileges (os.geteuid() == 0).
    - Hard timeouts on all child processes to ensure tests cannot hang.
"""

import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path

from precompile.cgroup import CgroupController


def _read_cpu_time(pid: int) -> float:
    """Read total CPU time (utime + stime) in seconds from /proc/<pid>/stat."""
    with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as f:
        fields = f.read().split()
        utime = int(fields[13])
        stime = int(fields[14])
    clk_tck = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    return (utime + stime) / clk_tck


class TestCgroupEffect(unittest.TestCase):
    """The Proof Test: Empirical verification of CFS priority boost under contention."""

    def setUp(self):
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            self.skipTest("Requires root privileges (run with 'wsl -u root')")

        self.ctrl = CgroupController(base_cgroup_path="/sys/fs/cgroup")
        self.ctrl.ensure_cpu_controller()
        self.target_cpu = 0

    def _make_busy_code(self, duration_s: float = 4.0, target_cpu: int = 0) -> str:
        return f"""
import os, time
os.sched_setaffinity(0, {{{target_cpu}}})
t0 = time.time()
while time.time() - t0 < {duration_s}:
    pass
# Sleep briefly so parent can read /proc/<pid>/stat before process exits
time.sleep(1.0)
"""

    def test_two_process_cgroup_weight_ratio(self):
        """Proof test Part 1: Pin boosted process (weight 500) and normal process (weight 100) to CPU 0.

        Theoretical CFS allocation: 500 / 100 = 5.0.
        Assert measured ratio >= 2.5.
        Repeat with weight 100: ratio should be near 1.0 (between 0.7 and 1.3).
        """
        grp_name = "precompile-res-proof-500"
        try:
            self.ctrl.create_group(grp_name, weight=500)

            # Spawn two busy processes pinned to same CPU
            code = self._make_busy_code(duration_s=4.0, target_cpu=self.target_cpu)
            p_boosted = subprocess.Popen(["python3", "-c", code])
            p_normal = subprocess.Popen(["python3", "-c", code])

            # Migrate boosted process to reservation cgroup
            self.ctrl.move_pid(grp_name, p_boosted.pid)

            # Wait for busy loop phase to complete (4.0s)
            time.sleep(4.2)

            t_boosted = _read_cpu_time(p_boosted.pid)
            t_normal = _read_cpu_time(p_normal.pid)

            p_boosted.wait(timeout=5)
            p_normal.wait(timeout=5)

            ratio = t_boosted / t_normal if t_normal > 0 else 0.0

            print("\n[PROOF TEST: Boosted (500) vs Normal (100) on 1 Core]")
            print(f"  Boosted Process CPU time: {t_boosted:.2f}s")
            print(f"  Normal  Process CPU time: {t_normal:.2f}s")
            print(f"  Ratio (boosted / normal): {ratio:.2f}")

            self.assertGreaterEqual(
                ratio,
                2.5,
                f"Boosted process ratio {ratio:.2f} was below required threshold 2.5",
            )
        finally:
            self.ctrl.cleanup_stale("precompile-res-proof-")

        # Repeat with weight 100 for the reservation (control run)
        grp_name_100 = "precompile-res-proof-100"
        try:
            self.ctrl.create_group(grp_name_100, weight=100)

            code = self._make_busy_code(duration_s=4.0, target_cpu=self.target_cpu)
            p1 = subprocess.Popen(["python3", "-c", code])
            p2 = subprocess.Popen(["python3", "-c", code])

            self.ctrl.move_pid(grp_name_100, p1.pid)
            time.sleep(4.2)

            t1 = _read_cpu_time(p1.pid)
            t2 = _read_cpu_time(p2.pid)

            p1.wait(timeout=5)
            p2.wait(timeout=5)

            ratio_100 = t1 / t2 if t2 > 0 else 0.0
            print("[PROOF TEST: Control (100) vs Normal (100) on 1 Core]")
            print(f"  Group 100 CPU time: {t1:.2f}s")
            print(f"  Normal    CPU time: {t2:.2f}s")
            print(f"  Ratio:              {ratio_100:.2f}")

            self.assertGreaterEqual(ratio_100, 0.7)
            self.assertLessEqual(ratio_100, 1.3)
        finally:
            self.ctrl.cleanup_stale("precompile-res-proof-")

    def test_eight_competitors_vs_one_boosted_process(self):
        """Proof test Part 2: 8 normal busy-loop competitors vs 1 boosted process on 1 CPU.

        Under CFS:
        Boosted group has weight 500.
        Init.scope has weight 100, shared among 8 competitor tasks.
        Boosted group should receive roughly 500 / (500 + 100) = ~83% of the single CPU's total time.
        Each competitor receives roughly 1/8 of 17% = ~2% of CPU time.
        """
        grp_name = "precompile-res-proof-8vs1"
        try:
            self.ctrl.create_group(grp_name, weight=500)

            code = self._make_busy_code(duration_s=4.0, target_cpu=self.target_cpu)
            p_boosted = subprocess.Popen(["python3", "-c", code])
            competitors = [subprocess.Popen(["python3", "-c", code]) for _ in range(8)]

            self.ctrl.move_pid(grp_name, p_boosted.pid)

            time.sleep(4.2)

            t_boosted = _read_cpu_time(p_boosted.pid)
            t_competitors = [_read_cpu_time(p.pid) for p in competitors]

            p_boosted.wait(timeout=5)
            for p in competitors:
                p.wait(timeout=5)

            avg_comp = sum(t_competitors) / len(t_competitors)
            total_cpu = t_boosted + sum(t_competitors)
            share_boosted = (t_boosted / total_cpu) * 100.0 if total_cpu > 0 else 0.0

            print("\n[PROOF TEST: 1 Boosted (500) vs 8 Normal (100) Competitors on 1 Core]")
            print(f"  Boosted Process CPU time:   {t_boosted:.2f}s ({share_boosted:.1f}% of total)")
            print(f"  Avg Competitor CPU time:    {avg_comp:.2f}s")
            print(f"  Ratio (boosted / avg comp): {t_boosted / avg_comp:.2f}")

            # Boosted should have substantially more CPU time than any single competitor
            self.assertGreaterEqual(t_boosted / avg_comp, 5.0)
            self.assertGreaterEqual(share_boosted, 60.0)
        finally:
            self.ctrl.cleanup_stale("precompile-res-proof-")


if __name__ == "__main__":
    unittest.main()
