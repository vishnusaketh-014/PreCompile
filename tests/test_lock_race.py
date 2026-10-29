import os
import threading
import time
import unittest
from pathlib import Path
from typing import List

from precompile.cgroup import CgroupController, FakeCgroupBackend
from precompile.reservation import ReservationManager
from precompile.watcher import ProcessInfo


def _interleave_hook():
    time.sleep(0.0002)


class TestLockLevelRace(unittest.TestCase):
    """Lock-level race test verifying atomic_claim concurrency protection."""

    def test_lock_race_atomic_on_vs_off_hook(self):
        # Use FakeCgroupBackend if not root, or real CgroupController if root
        if hasattr(os, "geteuid") and os.geteuid() == 0 and Path("/sys/fs/cgroup").exists():
            backend = CgroupController(base_cgroup_path="/sys/fs/cgroup")
        else:
            backend = FakeCgroupBackend()

        # Part 1: atomic_claim=True with 8 threads over 50 rounds
        for r in range(50):
            mgr = ReservationManager(
                cgroup_backend=backend,
                clock=time.monotonic,
                atomic_claim=True,
                sweep_window_s=0.0,
                starttime_reader=lambda pid: 1000,
            )
            rec = mgr.arm("proj_atomic", r, "default", time.monotonic(), 0.0, 5.0)
            mgr.tick()

            barrier = threading.Barrier(8)
            results = [False] * 8

            def worker(idx):
                barrier.wait()
                p = ProcessInfo(
                    pid=1000 + idx,
                    ppid=os.getpid(),
                    starttime_ticks=1000,
                    exe=Path("/usr/bin/gcc"),
                    cwd=Path("/tmp"),
                    cmdline=["gcc"],
                    t_seen_monotonic=time.monotonic(),
                    t_start_monotonic=time.monotonic(),
                )
                results[idx] = mgr.try_claim("proj_atomic", p)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            mgr.cancel(rec.id)
            winners = sum(1 for res in results if res)
            self.assertEqual(winners, 1, f"Round {r}: expected exactly 1 winner, got {winners}")

        # Part 2: atomic_claim=False with hook over 10 rounds gives > 1 winner
        dup_count = 0
        for r in range(10):
            mgr = ReservationManager(
                cgroup_backend=backend,
                clock=time.monotonic,
                atomic_claim=False,
                claim_interleave_hook=_interleave_hook,
                sweep_window_s=0.0,
                starttime_reader=lambda pid: 1000,
            )
            rec = mgr.arm("proj_non_atomic", r, "default", time.monotonic(), 0.0, 5.0)
            mgr.tick()

            barrier = threading.Barrier(4)
            results = [False] * 4

            def worker_off(idx):
                barrier.wait()
                p = ProcessInfo(
                    pid=2000 + idx,
                    ppid=os.getpid(),
                    starttime_ticks=1000,
                    exe=Path("/usr/bin/gcc"),
                    cwd=Path("/tmp"),
                    cmdline=["gcc"],
                    t_seen_monotonic=time.monotonic(),
                    t_start_monotonic=time.monotonic(),
                )
                results[idx] = mgr.try_claim("proj_non_atomic", p)

            threads = [threading.Thread(target=worker_off, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            mgr.cancel(rec.id)
            winners = sum(1 for res in results if res)
            if winners > 1:
                dup_count += 1

        self.assertGreater(dup_count, 0, "Expected at least one round with duplicate winners under atomic_claim=False + hook")


if __name__ == "__main__":
    unittest.main()
