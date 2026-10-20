"""Unit tests for Stage 10: Baseline conditions (c1, c2, c3) and ablations (A2, A3, A4, A5)."""

import argparse
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from precompile.cli import cmd_daemon
from precompile.config import AppConfig
from precompile.daemon import PreCompileDaemon
from precompile.gates import ArmDecision, ArmingGate
from precompile.matcher import ToolProfile
from precompile.monitor import SaveEvent
from precompile.predictor import LatencyPredictor, Prediction
from precompile.reservation import FakeCgroupBackend, ReservationManager, ReservationRecord, ReservationState
from precompile.safety import SafetyGovernor
from precompile.store import DataStore
from precompile.watcher import ProcessInfo


class TestConditionsAndAblations(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmpdir.name)
        self.db_path = self.tmp_path / "test.db"
        self.store = DataStore(db_path=self.db_path)
        self.store.add_project(
            project_id="test_proj",
            root_path=str(self.tmp_path),
            tool_profile_name="default",
        )
        self.fake_backend = FakeCgroupBackend()
        self.res_mgr = ReservationManager(
            cgroup_backend=self.fake_backend,
            clock=time.monotonic,
            sweep_window_s=0.0,
            starttime_reader=lambda pid: 1000,
        )
        self.safety = SafetyGovernor(clock=time.monotonic)
        self.safety.check_privileges = MagicMock(return_value=True)
        self._daemons = []

    def _create_daemon(self, **kwargs) -> PreCompileDaemon:
        defaults = {
            "db_path": self.db_path,
            "reservation_manager": self.res_mgr,
            "safety_governor": self.safety,
            "clock": time.monotonic,
        }
        defaults.update(kwargs)
        d = PreCompileDaemon(**defaults)
        self._daemons.append(d)
        return d

    def tearDown(self):
        for d in self._daemons:
            try:
                d.stop()
            except Exception:
                pass
        self.store.close()
        self.tmpdir.cleanup()

    def test_c1_reactive_no_save_prediction_or_arming(self):
        """Under c1-reactive, save events must not predict, arm, or intensify watcher."""
        daemon = self._create_daemon(condition="c1-reactive", actuate=True)
        save = SaveEvent(
            project_id="test_proj",
            path=self.tmp_path / "main.c",
            file_class="source",
            burst_size=1,
            t_save_wall=time.time(),
            t_save_monotonic=time.monotonic(),
        )
        daemon._on_save_event(save)

        # Confirm no pending saves and no reservations armed
        self.assertEqual(len(daemon.predictor._pending_saves), 0)
        self.assertEqual(len(self.res_mgr.list_active()), 0)

    @patch("precompile.daemon.read_psi_cpu")
    def test_c1_reactive_spawn_contended_vs_uncontended(self, mock_psi):
        """Under c1-reactive, matching process boosts if contention >= 10.0%, else observed."""
        daemon = self._create_daemon(condition="c1-reactive", actuate=True)
        # Mock PSI metrics: uncontended (5.0%)
        mock_metric = MagicMock()
        mock_metric.some_avg10 = 5.0
        mock_psi.return_value = mock_metric

        proc_uncontended = ProcessInfo(
            pid=2001,
            ppid=1,
            starttime_ticks=1000,
            exe=Path("/usr/bin/gcc"),
            cwd=self.tmp_path,
            cmdline=["gcc", "-c", "main.c"],
            t_seen_monotonic=time.monotonic(),
            t_start_monotonic=time.monotonic(),
        )
        daemon._handle_reactive_spawn("test_proj", proc_uncontended, proc_uncontended.t_start_monotonic)
        self.assertIn(2001, daemon._tracked_builds)
        self.assertEqual(daemon._tracked_builds[2001].condition, "observed")
        self.assertEqual(len(self.res_mgr.list_active()), 0)

        # Mock PSI metrics: contended (15.0%)
        mock_metric.some_avg10 = 15.0
        proc_contended = ProcessInfo(
            pid=2002,
            ppid=1,
            starttime_ticks=1000,
            exe=Path("/usr/bin/gcc"),
            cwd=self.tmp_path,
            cmdline=["gcc", "-c", "main.c"],
            t_seen_monotonic=time.monotonic(),
            t_start_monotonic=time.monotonic(),
        )
        daemon._handle_reactive_spawn("test_proj", proc_contended, proc_contended.t_start_monotonic)
        self.assertIn(2002, daemon._tracked_builds)
        self.assertEqual(daemon._tracked_builds[2002].condition, "reserved")
        self.assertIsNotNone(daemon._tracked_builds[2002].reservation_id)

    def test_c3_static_behavior(self):
        """Under c3-static, watcher is always intensified and matching spawn always claims."""
        daemon = self._create_daemon(condition="c3-static", actuate=True)
        save = SaveEvent(
            project_id="test_proj",
            path=self.tmp_path / "main.c",
            file_class="source",
            burst_size=1,
            t_save_wall=time.time(),
            t_save_monotonic=time.monotonic(),
        )
        daemon._on_save_event(save)
        self.assertEqual(len(daemon.predictor._pending_saves), 0)

        # Spawn matching process -> must claim boost without PSI check
        proc = ProcessInfo(
            pid=3001,
            ppid=1,
            starttime_ticks=1000,
            exe=Path("/usr/bin/gcc"),
            cwd=self.tmp_path,
            cmdline=["gcc", "-c", "main.c"],
            t_seen_monotonic=time.monotonic(),
            t_start_monotonic=time.monotonic(),
        )
        daemon._handle_static_spawn("test_proj", proc, proc.t_start_monotonic)
        self.assertIn(3001, daemon._tracked_builds)
        self.assertEqual(daemon._tracked_builds[3001].condition, "reserved")

    def test_ablation_a2_no_confidence_gate(self):
        """Under A2 (--ablation-no-confidence-gate), arms even if can_predict=False; n<2 uses [0, 5]."""
        daemon = self._create_daemon(
            condition="c2-full",
            ablation_no_confidence_gate=True,
            actuate=True,
        )
        save = SaveEvent(
            project_id="test_proj",
            path=self.tmp_path / "main.c",
            file_class="source",
            burst_size=1,
            t_save_wall=time.time(),
            t_save_monotonic=time.monotonic(),
        )
        daemon._on_save_event(save)

        # Under A2, prediction must be armed with fallback window [0.0, 5.0]
        pending = daemon.predictor._pending_saves.get("test_proj")
        self.assertIsNotNone(pending)
        self.assertEqual(pending.window_lo, 0.0)
        self.assertEqual(pending.window_hi, 5.0)

    def test_ablation_a3_no_contention_gate(self):
        """Under A3 (--ablation-no-contention-gate), contention gate is disabled."""
        daemon = self._create_daemon(
            condition="c2-full",
            ablation_no_contention_gate=True,
            actuate=True,
        )
        self.assertFalse(daemon.contention_gate_enabled)
        self.assertFalse(daemon._arming_gate.contention_gate_enabled)

    def test_ablation_a4_fixed_window(self):
        """Under A4 (--ablation-fixed-window LO,HI), predictor uses fixed window override."""
        daemon = self._create_daemon(
            condition="c2-full",
            ablation_fixed_window=(1.5, 4.5),
            actuate=True,
        )
        save = SaveEvent(
            project_id="test_proj",
            path=self.tmp_path / "main.c",
            file_class="source",
            burst_size=1,
            t_save_wall=time.time(),
            t_save_monotonic=time.monotonic(),
        )
        daemon._on_save_event(save)

        pending = daemon.predictor._pending_saves.get("test_proj")
        self.assertIsNotNone(pending)
        self.assertEqual(pending.window_lo, 1.5)
        self.assertEqual(pending.window_hi, 4.5)

    def test_ablation_a5_no_holdout(self):
        """Under A5 (--ablation-no-holdout), holdout probability is 0 and controller is disabled."""
        daemon = self._create_daemon(
            condition="c2-full",
            ablation_no_holdout=True,
            actuate=True,
        )
        self.assertEqual(daemon._arming_gate.holdout_probability, 0.0)
        self.assertIsNone(daemon.benefit_estimator)

    def test_flag_conflict_validation(self):
        """Daemon and CLI reject incompatible condition/ablation flag combinations."""
        # C1 rejects A2, A4, A5
        with self.assertRaises(ValueError):
            PreCompileDaemon(db_path=self.db_path, condition="c1-reactive", ablation_no_confidence_gate=True)
        with self.assertRaises(ValueError):
            PreCompileDaemon(db_path=self.db_path, condition="c1-reactive", ablation_fixed_window=(1.0, 2.0))
        with self.assertRaises(ValueError):
            PreCompileDaemon(db_path=self.db_path, condition="c1-reactive", ablation_no_holdout=True)

        # C3 rejects any ablation
        with self.assertRaises(ValueError):
            PreCompileDaemon(db_path=self.db_path, condition="c3-static", ablation_no_confidence_gate=True)
        with self.assertRaises(ValueError):
            PreCompileDaemon(db_path=self.db_path, condition="c3-static", ablation_no_contention_gate=True)

        # CLI validation check
        args = argparse.Namespace(
            subcommand="daemon",
            condition="c1-reactive",
            ablation_reactive=False,
            ablation_no_confidence_gate=True,
            ablation_no_contention_gate=False,
            no_contention_gate=False,
            ablation_fixed_window=None,
            ablation_no_holdout=False,
            log_level="info",
            db=str(self.db_path),
            pid_file=None,
        )
        rc = cmd_daemon(args)
        self.assertEqual(rc, 1)

    def test_migration_v5_and_daemon_runs_table(self):
        """Migration v5 idempotency and daemon_runs recording."""
        # Table should exist
        runs = self.store.list_daemon_runs()
        self.assertEqual(len(runs), 0)

        # Record a run
        run_id = self.store.record_daemon_run(
            condition="c2-full",
            flags_json=json.dumps({"actuate": True}),
            code_version="0.1.0",
        )
        self.assertGreaterEqual(run_id, 1)

        runs = self.store.list_daemon_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["condition"], "c2-full")
        self.assertEqual(runs[0]["code_version"], "0.1.0")

        # Verify idempotency by calling store.migrate again
        self.store.migrate()

        # Ensure table and record still intact
        runs2 = self.store.list_daemon_runs()
        self.assertEqual(len(runs2), 1)


if __name__ == "__main__":
    unittest.main()
