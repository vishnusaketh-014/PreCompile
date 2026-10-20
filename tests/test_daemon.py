"""Integration and component tests for PreCompileDaemon (precompile/daemon.py).

Verifies Requirements c, d, e, f, g, h, i, k, l from Step 6 specifications:
    c. Integration Scenario A: fake-gcc build-on-save with delay 0.6 +/- 0.03s, 14 cycles,
       in-process daemon. Asserts: >=10 accepted samples, learned mean within 0.1s of 0.6,
       shadow predictions scored, can_arm True, observed builds recorded with valid durations.
    d. Ordering inversion: delay 0.05s (faster than 0.15s debounce) paired correctly via spawn buffer.
    e. Spawn with t_start before save is ignored and does not consume pending save.
    f. Several matching spawns in one batch (make + cc parent/child) produce ONE sample with earliest start.
    g. Files generated during tracked build do not create SaveEvents (project suppression).
    h. Scenario C: 6 saves, no builds, short correlation window -> no_build outcomes recorded.
    i. Scenario B: irregular delays run through daemon without error; print gate state.
    k. Daemon performs no writes under /sys/fs/cgroup.
    l. read_psi_cpu parsing (real file + mocked sample strings).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

from precompile.config import AppConfig, MonitorConfig, PredictorConfig, WatcherConfig
from precompile.daemon import PreCompileDaemon
from precompile.gates import CPUContentionMetrics, parse_psi_cpu, read_psi_cpu
from precompile.matcher import ToolProfile, resolve_tool_profile
from precompile.monitor import SaveEvent
from precompile.store import DataStore
from precompile.watcher import ProcessInfo
from tests.test_watcher import create_fake_tool


class TestDaemon(unittest.TestCase):
    """Test suite covering PreCompileDaemon orchestration and scenarios."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(dir="/tmp", prefix="precompile_daemon_test_")
        self.proj_dir = Path(self.temp_dir) / "demo_proj"
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(self.temp_dir) / "daemon_test.db"
        self.bin_dir = self.proj_dir / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.fake_gcc = create_fake_tool(self.bin_dir / "gcc")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_l_read_psi_cpu_parsing(self) -> None:
        """Requirement l: read_psi_cpu parsing (real file + mocked sample strings)."""
        # 1. Mock complete sample string
        sample_psi = (
            "some avg10=2.50 avg60=1.20 avg300=0.80 total=123456\n"
            "full avg10=0.40 avg60=0.20 avg300=0.10 total=45678\n"
        )
        metrics = parse_psi_cpu(sample_psi)
        self.assertIsNotNone(metrics)
        self.assertAlmostEqual(metrics.some_avg10, 2.50)
        self.assertAlmostEqual(metrics.some_avg60, 1.20)
        self.assertAlmostEqual(metrics.some_avg300, 0.80)
        self.assertEqual(metrics.some_total, 123456)
        self.assertAlmostEqual(metrics.full_avg10, 0.40)
        self.assertEqual(metrics.full_total, 45678)

        # 2. Mock string missing 'full' line (e.g. single-core / older kernels)
        some_only_psi = "some avg10=1.15 avg60=0.90 avg300=0.50 total=999\n"
        metrics_some = parse_psi_cpu(some_only_psi)
        self.assertIsNotNone(metrics_some)
        self.assertAlmostEqual(metrics_some.some_avg10, 1.15)
        self.assertAlmostEqual(metrics_some.full_avg10, 0.0)

        # 3. Invalid or empty text returns None
        self.assertIsNone(parse_psi_cpu(""))
        self.assertIsNone(parse_psi_cpu("invalid garbage\n"))

        # 4. Real file read (should return metrics or None if not supported by host kernel)
        real_metrics = read_psi_cpu()
        if real_metrics is not None:
            self.assertIsInstance(real_metrics, CPUContentionMetrics)
            self.assertGreaterEqual(real_metrics.some_avg10, 0.0)

    def test_k_daemon_no_cgroup_writes(self) -> None:
        """Requirement k: Daemon performs no writes under /sys/fs/cgroup in observation mode and falls back cleanly."""
        # Non-root actuate falls back gracefully to observation-only without error (Part D.d)
        daemon_fallback = PreCompileDaemon(db_path=self.db_path, actuate=True)
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            self.assertFalse(daemon_fallback.actuate)

        # Ensure cgroup module is not invoked during standard lifecycle in observation mode
        daemon = PreCompileDaemon(db_path=self.db_path, actuate=False)
        with patch("os.mkdir") as mock_mkdir, patch("builtins.open", create=True) as mock_open:
            daemon.start()
            time.sleep(0.05)
            daemon.stop()

            # Verify no attempted accesses under /sys/fs/cgroup
            for call_args in mock_mkdir.call_args_list:
                path_str = str(call_args[0][0])
                self.assertNotIn("/sys/fs/cgroup", path_str)

    def test_c_scenario_a_integration(self) -> None:
        """Requirement c: Scenario A integration with fake-gcc 0.6 +/- 0.03s delay over 14 cycles.

        Asserts >= 10 accepted samples, learned mean within 0.1s of 0.6s, shadow predictions scored,
        can_arm True, and observed builds recorded with sensible durations.
        """
        src_file = self.proj_dir / "main.c"
        src_file.write_text("int main(){return 0;}\n", encoding="utf-8")

        # Register project with tool profile
        with DataStore(self.db_path) as store:
            store.add_project("demo", str(self.proj_dir), "custom:gcc")

        # Use fast polling intervals for test
        cfg = AppConfig(
            monitor=MonitorConfig(debounce_seconds=0.10),
            watcher=WatcherConfig(poll_interval_normal_ms=20, poll_interval_intensified_ms=10),
            predictor=PredictorConfig(alpha=0.2, min_samples=5, min_predictions=5),
        )

        daemon = PreCompileDaemon(config=cfg, db_path=self.db_path)
        daemon.start()

        # Run 14 cycles of save -> wait ~0.6s -> spawn fake-gcc 0.08s
        delays = [0.60, 0.62, 0.58, 0.61, 0.59, 0.60, 0.62, 0.58, 0.61, 0.59, 0.60, 0.61, 0.59, 0.60]

        try:
            for i, delay in enumerate(delays, start=1):
                # 1. Edit file via temp-rename
                new_content = f"int main(){{ /* cycle {i} */ return 0; }}\n"
                with tempfile.NamedTemporaryFile("w", dir=self.proj_dir, delete=False) as tf:
                    tf.write(new_content)
                    temp_path = tf.name
                os.replace(temp_path, src_file)

                # 2. Wait delay
                time.sleep(delay)

                # 3. Spawn fake gcc inside proj_dir (sleeps for 0.15s)
                proc = subprocess.Popen(
                    [str(self.fake_gcc), "0.15"],
                    cwd=str(self.proj_dir),
                )
                proc.wait()

                # 4. Inter-cycle gap allowing 0.3s save suppression tail to clear
                time.sleep(0.4)

            # Allow trailing housekeeping to process completion
            time.sleep(0.3)
            desc = daemon.predictor.describe(("demo", "source", "custom:gcc"))
        finally:
            daemon.stop()

        with DataStore(self.db_path) as store:
            samples = store.recent_samples("demo", "source", "custom:gcc", n=50)
            self.assertGreaterEqual(len(samples), 10, f"Expected >= 10 accepted samples, got {len(samples)}")

            # Check learned model state
            state = store.load_model_state(("demo", "source", "custom:gcc"))
            self.assertIsNotNone(state)
            n, mean, var = state
            self.assertGreaterEqual(n, 10)
            self.assertAlmostEqual(mean, 0.60, delta=0.10, msg=f"Learned mean {mean:.3f}s not within 0.1s of 0.60s")

            # Check shadow predictions scored
            scored_preds = store.get_recent_outcomes("demo", limit=20)
            self.assertGreaterEqual(len(scored_preds), 5, "Expected at least 5 scored shadow predictions")

            # Check can_arm is True
            self.assertTrue(desc["gate_result"]["can_predict"])
            self.assertTrue(desc["gate_result"]["can_arm"])

            # Check observed builds recorded
            builds = store.builds_by_condition("demo", "observed")
            self.assertGreaterEqual(len(builds), 8, f"Expected >= 8 observed builds, got {len(builds)}")
            for b in builds:
                self.assertGreater(b.duration_s, 0.05, "Build duration should reflect real execution")

    def test_d_ordering_inversion_fast_delay_paired(self) -> None:
        """Requirement d: Delay 0.05s (faster than 0.15s debounce) is paired correctly via spawn buffer."""
        src_file = self.proj_dir / "main.c"
        src_file.write_text("int a = 1;\n", encoding="utf-8")

        with DataStore(self.db_path) as store:
            store.add_project("proj_fast", str(self.proj_dir), "custom:gcc")

        # 0.15s debounce on monitor
        cfg = AppConfig(
            monitor=MonitorConfig(debounce_seconds=0.15),
            watcher=WatcherConfig(poll_interval_normal_ms=10),
        )
        daemon = PreCompileDaemon(config=cfg, db_path=self.db_path)
        daemon.start()

        try:
            # Atomic save
            with tempfile.NamedTemporaryFile("w", dir=self.proj_dir, delete=False) as tf:
                tf.write("int a = 2;\n")
                temp_path = tf.name
            os.replace(temp_path, src_file)

            # Wait only 0.05s (< 0.15s debounce) and spawn fake-gcc immediately
            time.sleep(0.05)
            proc = subprocess.Popen([str(self.fake_gcc), "0.15"], cwd=str(self.proj_dir))
            proc.wait()

            # Wait for debounce and daemon processing
            time.sleep(0.3)
        finally:
            daemon.stop()

        with DataStore(self.db_path) as store:
            samples = store.recent_samples("proj_fast", "source", "custom:gcc", n=10)
            self.assertGreaterEqual(len(samples), 1, "Fast build before debounce should be paired from spawn buffer")
            delay = samples[0].delay_s
            self.assertGreater(delay, 0.0)
            self.assertLess(delay, 0.20, f"Expected paired delay around 0.05-0.10s, got {delay:.3f}s")

    def test_e_spawn_before_save_ignored(self) -> None:
        """Requirement e: Spawn with t_start before save is ignored and does not consume pending save."""
        with DataStore(self.db_path) as store:
            store.add_project("proj_e", str(self.proj_dir), "default")

        daemon = PreCompileDaemon(db_path=self.db_path)

        # 1. Spawn occurs at t=100.0
        early_proc = ProcessInfo(
            pid=2001,
            ppid=1,
            starttime_ticks=1000,
            exe=Path("/usr/bin/gcc"),
            cwd=self.proj_dir,
            cmdline=["gcc", "-c", "main.c"],
            t_seen_monotonic=100.0,
            t_start_monotonic=100.0,
        )
        daemon._on_process_batch([early_proc])

        # 2. Save occurs at t=102.0 (after spawn)
        save_ev = SaveEvent("proj_e", self.proj_dir / "main.c", "source", 102.0, 102.0)
        daemon._on_save_event(save_ev)

        # Pending save should still be open (not consumed by early spawn)
        pending = daemon.predictor._pending_saves.get("proj_e")
        self.assertIsNotNone(pending)
        self.assertFalse(pending.spawn_handled)
        self.assertEqual(pending.t_save_monotonic, 102.0)

        # 3. Legitimate spawn at t=103.8 consumes the save
        valid_proc = ProcessInfo(
            pid=2002,
            ppid=1,
            starttime_ticks=1038,
            exe=Path("/usr/bin/gcc"),
            cwd=self.proj_dir,
            cmdline=["gcc", "-c", "main.c"],
            t_seen_monotonic=103.8,
            t_start_monotonic=103.8,
        )
        daemon._on_process_batch([valid_proc])

        # Now save is consumed with delay = 1.8s
        with DataStore(self.db_path) as store:
            samples = store.recent_samples("proj_e", "source", "default", n=10)
            self.assertEqual(len(samples), 1)
            self.assertAlmostEqual(samples[0].delay_s, 1.8, places=4)

    def test_f_multiple_matching_spawns_in_one_batch_produces_one_sample(self) -> None:
        """Requirement f: Several matching spawns in one batch produce ONE sample with earliest start."""
        with DataStore(self.db_path) as store:
            store.add_project("proj_f", str(self.proj_dir), "default")

        daemon = PreCompileDaemon(db_path=self.db_path)

        # Pending save at t=10.0
        daemon._on_save_event(SaveEvent("proj_f", self.proj_dir / "main.c", "source", 10.0, 10.0))

        # Make (parent, start=11.5) and gcc (child, start=11.8) arrive in the same scan batch
        make_proc = ProcessInfo(
            pid=3001,
            ppid=1,
            starttime_ticks=1150,
            exe=Path("/usr/bin/make"),
            cwd=self.proj_dir,
            cmdline=["make"],
            t_seen_monotonic=11.8,
            t_start_monotonic=11.5,
        )
        gcc_proc = ProcessInfo(
            pid=3002,
            ppid=3001,
            starttime_ticks=1180,
            exe=Path("/usr/bin/gcc"),
            cwd=self.proj_dir,
            cmdline=["gcc", "main.c"],
            t_seen_monotonic=11.8,
            t_start_monotonic=11.8,
        )

        daemon.watcher._process_table[3001] = make_proc
        daemon.watcher._parent_map[3002] = 3001

        # Deliver both in single batch
        daemon._on_process_batch([gcc_proc, make_proc])

        # Exactly ONE sample recorded with delay = 11.5 - 10.0 = 1.5s (from make)
        with DataStore(self.db_path) as store:
            samples = store.recent_samples("proj_f", "source", "default", n=10)
            self.assertEqual(len(samples), 1)
            self.assertAlmostEqual(samples[0].delay_s, 1.5, places=4)

    def test_g_files_generated_during_build_suppressed(self) -> None:
        """Requirement g: Files generated during tracked build do not create SaveEvents."""
        src_file = self.proj_dir / "main.c"
        src_file.write_text("int g = 1;\n", encoding="utf-8")

        with DataStore(self.db_path) as store:
            store.add_project("proj_g", str(self.proj_dir), "custom:gcc")

        cfg = AppConfig(
            monitor=MonitorConfig(debounce_seconds=0.05),
            watcher=WatcherConfig(poll_interval_normal_ms=10),
        )
        daemon = PreCompileDaemon(config=cfg, db_path=self.db_path)
        daemon.start()

        try:
            # Save to start cycle
            src_file.write_text("int g = 2;\n", encoding="utf-8")
            time.sleep(0.1)

            # Spawn fake-gcc running for 0.4s
            proc = subprocess.Popen([str(self.fake_gcc), "0.4"], cwd=str(self.proj_dir))
            time.sleep(0.1)  # wait for daemon to register active build and suppress

            # Touch another source file while build is running
            other_file = self.proj_dir / "other.c"
            other_file.write_text("/* generated by build */\n", encoding="utf-8")

            proc.wait()
            # Wait for completion + 0.3s tail to pass
            time.sleep(0.5)
        finally:
            daemon.stop()

        # Check that 'other.c' save was dropped by suppression
        with DataStore(self.db_path) as store:
            samples = store.recent_samples("proj_g", "source", "custom:gcc", n=10)
            # Only the first real build was matched
            self.assertEqual(len(samples), 1)

    def test_h_scenario_c_save_without_build_expires(self) -> None:
        """Requirement h: Scenario C: 6 saves with no builds; correlation window expires to no_build."""
        with DataStore(self.db_path) as store:
            store.add_project("proj_h", str(self.proj_dir), "default")
            # Seed 5 initial hits so predictions are actively armed/scored
            for _ in range(5):
                store.record_sample("proj_h", "source", "default", delay_s=1.5, accepted=True)
            store.save_model_state(("proj_h", "source", "default"), n=5, mean=1.5, var=0.001)

        sim_time = 100.0

        def clock():
            return sim_time

        daemon = PreCompileDaemon(
            db_path=self.db_path,
            correlation_window_s=1.0,  # short 1.0s correlation window
            clock=clock,
        )

        for i in range(6):
            sim_time += 10.0
            ev = SaveEvent("proj_h", self.proj_dir / "main.c", "source", sim_time, sim_time)
            daemon._on_save_event(ev)

            # Advance past 1.0s correlation window without spawn
            sim_time += 1.5
            daemon.predictor.expire_pending(now=sim_time)

        daemon.stop()

        with DataStore(self.db_path) as store:
            outcomes = store.get_recent_outcomes("proj_h", limit=10)
            no_build_outcomes = [o for o in outcomes if o.outcome == "no_build"]
            self.assertGreaterEqual(len(no_build_outcomes), 5)

    def test_i_scenario_b_irregular_delays(self) -> None:
        """Requirement i: Scenario B irregular delays run without error; prints gate state."""
        with DataStore(self.db_path) as store:
            store.add_project("proj_b", str(self.proj_dir), "default")

        sim_time = 100.0

        def clock():
            return sim_time

        daemon = PreCompileDaemon(db_path=self.db_path, clock=clock)
        irregular_delays = [0.8, 14.5, 2.1, 18.0, 5.2, 12.3, 0.9, 16.2, 3.4, 19.1]

        for i, d in enumerate(irregular_delays, start=1):
            sim_time += 30.0
            daemon._on_save_event(SaveEvent("proj_b", self.proj_dir / "main.c", "source", sim_time, sim_time))
            sim_time += d
            proc = ProcessInfo(
                pid=4000 + i,
                ppid=1,
                starttime_ticks=int(sim_time * 100),
                exe=Path("/usr/bin/gcc"),
                cwd=self.proj_dir,
                cmdline=["gcc", "main.c"],
                t_seen_monotonic=sim_time,
                t_start_monotonic=sim_time,
            )
            daemon._on_process_batch([proc])

        desc = daemon.predictor.describe(("proj_b", "source", "default"))
        print("\n[SCENARIO B GATE STATE PRINTOUT]")
        print(f"  Samples: {desc['n']}")
        print(f"  Mean: {desc['mean']:.2f}s, Sigma: {desc['sigma']:.2f}s, CV: {desc['cv']}")
        print(f"  Can Predict: {desc['gate_result']['can_predict']}")
        print(f"  Can Arm:     {desc['gate_result']['can_arm']}")
        print(f"  Reasons:     {desc['gate_result']['reasons']}")
        daemon.stop()

    def test_accurate_build_timing(self) -> None:
        """Part A.2: Build duration recorded as exit - t_start within 30ms of real run time (0.5s)."""
        src_file = self.proj_dir / "main.c"
        src_file.write_text("int main(){return 0;}\n", encoding="utf-8")
        with DataStore(self.db_path) as store:
            store.add_project("proj_timing", str(self.proj_dir), "custom:gcc")

        daemon = PreCompileDaemon(db_path=self.db_path)
        daemon.start()

        try:
            # Trigger save event
            with tempfile.NamedTemporaryFile("w", dir=self.proj_dir, delete=False) as tf:
                tf.write("int main(){ /* timing test */ return 0; }\n")
                temp_path = tf.name
            os.replace(temp_path, src_file)
            time.sleep(0.2)

            # Spawn fake_gcc 0.5s
            p = subprocess.Popen([str(self.fake_gcc), "0.5"], cwd=str(self.proj_dir))
            p.wait(timeout=2.0)
            time.sleep(0.15)
        finally:
            daemon.stop()

        # Check recorded build duration in store
        with DataStore(self.db_path) as store:
            builds = store.builds_by_condition("proj_timing", "observed")
            self.assertGreaterEqual(len(builds), 1)
            b = builds[0]
            # Duration must be within 30ms (0.030s) of expected 0.5s
            self.assertAlmostEqual(b.duration_s, 0.50, delta=0.030)


if __name__ == "__main__":
    unittest.main()
