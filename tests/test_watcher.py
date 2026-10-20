"""Unit tests for Stage 2 Process Watcher (precompile.watcher).

All tests execute in temporary directories under /tmp on native Linux fs.
"""

import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from precompile.watcher import (
    ProcessInfo,
    ProcessWatcher,
    compute_t_start_monotonic,
    parse_stat_content,
)


def create_fake_tool(target_path: Path) -> Path:
    """Create a standalone executable for testing by copying /bin/sleep or compiling."""
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # If a C compiler is available, compile a real standalone sleeper
    for cc in ["gcc", "clang", "cc"]:
        if shutil.which(cc):
            try:
                subprocess.run(
                    [cc, "-x", "c", "-", "-o", str(target_path)],
                    input=b"#include <unistd.h>\n#include <stdlib.h>\nint main(int c, char** v){ if(c>1) usleep((useconds_t)(atof(v[1])*1000000.0)); else usleep(1000000); return 0; }\n",
                    check=True,
                    capture_output=True,
                )
                os.chmod(str(target_path), 0o755)
                return target_path
            except Exception:
                continue

    # Otherwise fallback to copying /bin/sleep
    shutil.copyfile("/bin/sleep", str(target_path), follow_symlinks=True)
    os.chmod(str(target_path), 0o755)
    return target_path


class TestProcessWatcher(unittest.TestCase):
    """Test suite covering requirements for precompile.watcher."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(dir="/tmp", prefix="precompile_watch_test_")
        self.tool_path = create_fake_tool(Path(self.temp_dir) / "fake-gcc")
        self.watcher: Optional[ProcessWatcher] = None
        self.spawned_procs: list = []

    def tearDown(self) -> None:
        for p in self.spawned_procs:
            try:
                p.kill()
                p.wait()
            except Exception:
                pass
        if self.watcher is not None:
            try:
                self.watcher.stop()
            except Exception:
                pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_a_spawned_process_detected_with_correct_metadata(self) -> None:
        """Requirement a: a spawned process is detected with correct pid, ppid, exe, cwd, and t_start_monotonic."""
        self.watcher = ProcessWatcher(poll_interval_normal_ms=10)
        self.watcher.start()

        time.sleep(0.05)  # Allow watcher to complete baseline

        t_before = time.monotonic()
        proc = subprocess.Popen(
            [str(self.tool_path), "2"],
            cwd=self.temp_dir,
        )
        self.spawned_procs.append(proc)
        t_after = time.monotonic()

        # Poll until the process is discovered or timeout
        detected: Optional[ProcessInfo] = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            events = self.watcher.poll_new_processes(timeout_ms=50)
            for ev in events:
                if ev.pid == proc.pid:
                    detected = ev
                    break
            if detected is not None:
                break
            time.sleep(0.02)

        self.assertIsNotNone(detected, f"Process {proc.pid} was not detected by watcher")
        self.assertEqual(detected.pid, proc.pid)
        self.assertEqual(detected.ppid, os.getpid())
        self.assertEqual(os.path.realpath(detected.exe), os.path.realpath(self.tool_path))
        self.assertEqual(os.path.realpath(detected.cwd), os.path.realpath(self.temp_dir))

        # Check t_start_monotonic is within sensible tolerance of real spawn time
        # Clock ticks are 10 ms; tolerance of 0.25 s covers scheduler/tick rounding
        self.assertGreaterEqual(detected.t_start_monotonic, t_before - 0.25)
        self.assertLessEqual(detected.t_start_monotonic, t_after + 0.25)
        self.assertIn(detected.t_start_source, ("boottime", "seen_monotonic"))

    def test_b_process_exiting_mid_read_counted_as_missed_without_crash(self) -> None:
        """Requirement b: a process that exits before being read is counted as missed and does not crash the watcher."""
        self.watcher = ProcessWatcher(poll_interval_normal_ms=10, baseline_existing=True)
        self.watcher.init_known_pids()

        # Inject an invalid/nonexistent PID directly to simulate mid-read disappearance
        fake_dead_pid = 99999999
        self.watcher._known_pids.discard(fake_dead_pid)

        # Calling _inspect_pid on non-existent PID should return None without error
        result = self.watcher._inspect_pid(fake_dead_pid)
        self.assertIsNone(result)

        # Simulate a scan discovering this dead PID
        initial_missed = self.watcher.get_stats().processes_missed

        # Mock os.listdir in watcher's proc path to include fake_dead_pid
        orig_listdir = os.listdir
        try:
            def mock_listdir(path):
                entries = orig_listdir(path)
                entries.append(str(fake_dead_pid))
                return entries

            os.listdir = mock_listdir
            self.watcher.scan_once()
        finally:
            os.listdir = orig_listdir

        stats = self.watcher.get_stats()
        self.assertGreaterEqual(stats.processes_missed, initial_missed + 1)

    def test_c_intensified_mode_scans_faster_than_normal_mode(self) -> None:
        """Requirement c: intensified mode scans faster than normal mode."""
        # Normal mode: 50 ms interval
        # Intensified mode: 10 ms interval
        self.watcher = ProcessWatcher(
            poll_interval_normal_ms=50,
            poll_interval_intensified_ms=10,
        )

        self.watcher.start()
        time.sleep(0.20)
        normal_scans = self.watcher.get_stats().scans_performed

        self.watcher.set_intensified(True)
        self.assertTrue(self.watcher.is_intensified())

        t_start = time.monotonic()
        time.sleep(0.20)
        total_scans = self.watcher.get_stats().scans_performed
        intensified_scans = total_scans - normal_scans

        self.watcher.stop()

        # Intensified mode (10 ms) should perform significantly more scans than normal mode (50 ms)
        self.assertGreater(
            intensified_scans,
            normal_scans,
            f"Expected intensified scans ({intensified_scans}) > normal scans ({normal_scans})",
        )

    def test_d_stat_parsing_handles_spaces_and_parentheses_in_comm(self) -> None:
        """Requirement d: stat parsing works for a process whose comm contains spaces and parentheses."""
        sample_stat = (
            "12345 (fake (gcc) tool) S 5678 12345 12345 0 0 0 0 0 0 0 0 0 0 0 "
            "20 0 1 0 71841 1234567 100 18446744073709551615 0 0 0 0 0"
        )
        pid, ppid, starttime_ticks, comm = parse_stat_content(sample_stat)

        self.assertEqual(pid, 12345)
        self.assertEqual(ppid, 5678)
        self.assertEqual(starttime_ticks, 71841)
        self.assertEqual(comm, "fake (gcc) tool")

    def test_compute_t_start_monotonic_accuracy_and_fallback(self) -> None:
        """Verify boottime computation and fallback path."""
        t_now = time.monotonic()
        # Normal computation should return 'boottime' and a timestamp close to monotonic
        t_start, source = compute_t_start_monotonic(starttime_ticks=1000, t_seen_monotonic=t_now)
        self.assertIn(source, ("boottime", "seen_monotonic"))
        self.assertIsInstance(t_start, float)

        # Fallback path when clk_tck fails or invalid
        t_fallback, fallback_source = compute_t_start_monotonic(
            starttime_ticks=1000,
            t_seen_monotonic=t_now,
            clk_tck=0,  # ZeroDivisionError forces fallback
        )
        self.assertEqual(fallback_source, "seen_monotonic")
        self.assertEqual(t_fallback, t_now)

    def test_overhead_stats_tracking(self) -> None:
        """Verify monitoring overhead statistics are properly accumulated."""
        self.watcher = ProcessWatcher(poll_interval_normal_ms=10)
        self.watcher.start()
        time.sleep(0.08)
        self.watcher.stop()

        stats = self.watcher.get_stats()
        self.assertGreater(stats.scans_performed, 0)
        self.assertGreater(stats.total_scan_duration_s, 0.0)
        self.assertGreaterEqual(stats.max_scan_duration_s, 0.0)
        self.assertGreaterEqual(stats.avg_scan_duration_ms, 0.0)

    def test_clean_start_stop_no_thread_leakage(self) -> None:
        """Verify starting and stopping does not leak background threads."""
        threads_before = threading.active_count()
        watcher = ProcessWatcher(poll_interval_normal_ms=10)
        watcher.start()
        self.assertEqual(threading.active_count(), threads_before + 1)
        watcher.stop()
        self.assertEqual(threading.active_count(), threads_before)

    def test_grace_period_retention_and_expiry(self) -> None:
        """Verify an exited process stays in table during grace period and is removed after."""
        self.watcher = ProcessWatcher(
            poll_interval_normal_ms=10,
            grace_period_seconds=0.15,
        )
        self.watcher.start()
        time.sleep(0.04)

        # Spawn a process that stays alive long enough to be discovered
        p = subprocess.Popen([str(self.tool_path), "0.5"], cwd=self.temp_dir)
        self.spawned_procs.append(p)
        pid = p.pid

        # Wait until watcher has discovered it
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if pid in self.watcher.get_process_table():
                break
            time.sleep(0.01)

        self.assertIn(pid, self.watcher.get_process_table(), "Process must be seen alive by watcher")

        # Now terminate process so it exits and leaves /proc
        p.kill()
        p.wait()

        # Immediate check: process is dead, but should still be in table during grace period
        time.sleep(0.03)
        table = self.watcher.get_process_table()
        self.assertIn(pid, table, "Terminated process should remain in table during grace period")
        self.assertIn(pid, self.watcher.get_parent_map())

        # Wait for grace period (0.15s) to expire
        time.sleep(0.20)
        table_after = self.watcher.get_process_table()
        self.assertNotIn(pid, table_after, "Process should be pruned after grace period expires")
        self.assertNotIn(pid, self.watcher.get_parent_map())

    def test_table_hard_cap_enforcement(self) -> None:
        """Verify the process table does not grow without bound beyond max_table_size."""
        max_size = 5
        self.watcher = ProcessWatcher(
            poll_interval_normal_ms=10,
            max_table_size=max_size,
            grace_period_seconds=60.0,  # Long grace period so pruning is driven by hard cap
        )
        self.watcher.init_known_pids()

        # Artificially populate table with 10 dummy entries
        with self.watcher._lock:
            for dummy_pid in range(9000, 9010):
                self.watcher._process_table[dummy_pid] = ProcessInfo(
                    pid=dummy_pid,
                    ppid=1,
                    starttime_ticks=100,
                    exe=Path("/bin/fake"),
                    cwd=Path("/tmp"),
                    cmdline=[],
                    t_seen_monotonic=time.monotonic(),
                    t_start_monotonic=time.monotonic(),
                )
                self.watcher._parent_map[dummy_pid] = 1
                self.watcher._last_seen_alive[dummy_pid] = time.monotonic() - (9010 - dummy_pid)

        # Perform one scan which triggers cap enforcement
        self.watcher.scan_once()
        table = self.watcher.get_process_table()
        self.assertLessEqual(len(table), max_size, f"Table size {len(table)} exceeds cap {max_size}")


    def test_exec_in_place_detection(self) -> None:
        """Part A.1: Watcher detects in-place exec (e.g. bash sleep -> exec fake-gcc)."""
        self.watcher = ProcessWatcher(
            poll_interval_normal_ms=10,
            poll_interval_intensified_ms=5,
        )
        self.watcher.set_intensified(True)
        self.watcher.start()

        # Run `bash -c 'sleep 0.2; exec <fake-gcc> 0.3'`
        p = subprocess.Popen(
            ["bash", "-c", f"sleep 0.2; exec {self.tool_path} 0.3"],
            cwd=self.temp_dir,
        )
        self.spawned_procs.append(p)

        # Wait for execution to finish
        p.wait(timeout=2.0)
        time.sleep(0.05)
        self.watcher.stop()

        # Check process table: PID should be detected as tool and marked exec_of_known_pid=True
        table = self.watcher.get_process_table()
        self.assertIn(p.pid, table, f"PID {p.pid} must be in process table")
        pinfo = table[p.pid]
        self.assertEqual(pinfo.exe.name, self.tool_path.name)
        self.assertTrue(pinfo.exec_of_known_pid, "exec_of_known_pid must be True after exec")

        # Also test taskset -c 0 <fake-gcc> 0.3
        self.watcher = ProcessWatcher(
            poll_interval_normal_ms=10,
            poll_interval_intensified_ms=5,
        )
        self.watcher.set_intensified(True)
        self.watcher.start()

        p2 = subprocess.Popen(
            ["taskset", "-c", "0", str(self.tool_path), "0.3"],
            cwd=self.temp_dir,
        )
        self.spawned_procs.append(p2)
        p2.wait(timeout=2.0)
        time.sleep(0.05)
        self.watcher.stop()

        table2 = self.watcher.get_process_table()
        self.assertIn(p2.pid, table2, f"PID {p2.pid} must be in process table")
        self.assertEqual(table2[p2.pid].exe.name, self.tool_path.name)

    def test_mode_scans_and_time_tracking(self) -> None:
        """Step 11a Part A.3: Watcher tracks scans and elapsed time across normal and intensified modes."""
        watcher = ProcessWatcher(poll_interval_normal_ms=10, poll_interval_intensified_ms=5)
        watcher.scan_once()
        watcher.scan_once()

        stats1 = watcher.get_stats()
        self.assertEqual(stats1.scans_normal, 2)
        self.assertEqual(stats1.scans_intensified, 0)
        self.assertGreater(stats1.time_normal_s, 0.0)

        watcher.set_intensified(True)
        watcher.scan_once()
        watcher.scan_once()
        watcher.scan_once()

        stats2 = watcher.get_stats()
        self.assertEqual(stats2.scans_normal, 2)
        self.assertEqual(stats2.scans_intensified, 3)
        self.assertEqual(stats2.scans_performed, 5)
        self.assertGreater(stats2.time_intensified_s, 0.0)
        self.assertGreater(stats2.scans_per_sec_intensified, 0.0)


if __name__ == "__main__":
    unittest.main()
