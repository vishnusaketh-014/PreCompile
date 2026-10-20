"""Unit tests for Stage 10 Safety, Bounded Harm, and Fallback (precompile/safety.py).

Tests:
    a. AST & token scan forbidden real-time scheduling APIs: asserts no calls to
       sched_setscheduler, SCHED_FIFO, SCHED_RR, SCHED_DEADLINE, chrt, setpriority, os.nice.
    b. Runtime inspection of process scheduling policy: rejects FIFO/RR/DEADLINE, accepts OTHER/BATCH/IDLE.
    c. Weight ceiling validation: clamps to [1, 1000].
    d. Rolling boost budget: exhausts budget with rapid claims, refuses boosts, refills as time advances.
    e. Budget persistence in DataStore: reservations table survives restarts and calculates rolling budget.
    f. Circuit breaker trips after 5 consecutive failures, enters 300s cooldown, recovers after cooldown.
    g. Kill switch pause/resume mechanism via flag file.
    h. Privilege and cgroup degradation checks with specific error codes.
"""

import ast
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from precompile.safety import (
    DEFAULT_MAX_WEIGHT,
    SafetyGovernor,
    check_process_scheduling,
)
from precompile.store import DataStore


class TestSafety(unittest.TestCase):
    """Test suite for SafetyGovernor and fair-share scheduling invariants."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "test_safety.db"
        self.store = DataStore(db_path=self.db_path)
        self.store.add_project("demo", "/tmp/demo", "default")

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_a_ast_no_realtime_scheduling_apis(self):
        """Part B.1: AST scan asserting NO code in precompile/ calls real-time scheduling APIs."""
        precompile_dir = Path(__file__).resolve().parent.parent / "precompile"
        forbidden_names = {
            "sched_setscheduler",
            "SCHED_FIFO",
            "SCHED_RR",
            "SCHED_DEADLINE",
            "setpriority",
            "nice",
        }

        py_files = list(precompile_dir.glob("*.py"))
        self.assertGreater(len(py_files), 5, "Should find precompile Python files")

        violations = []

        for py_path in py_files:
            source = py_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_path))

            for node in ast.walk(tree):
                # Check function/method calls: os.sched_setscheduler(), os.nice(), setpriority()
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id in forbidden_names:
                        violations.append((py_path.name, node.lineno, func.id))
                    elif isinstance(func, ast.Attribute) and func.attr in forbidden_names:
                        violations.append((py_path.name, node.lineno, func.attr))

                # Check attribute accesses or imports of real-time constants
                if isinstance(node, ast.Attribute):
                    if node.attr in {"SCHED_FIFO", "SCHED_RR", "SCHED_DEADLINE"}:
                        violations.append((py_path.name, node.lineno, node.attr))

                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in forbidden_names:
                            violations.append((py_path.name, node.lineno, alias.name))

            # Also verify no string literals invoke 'chrt' as an executable in commands
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    words = node.value.split()
                    if "chrt" in words:
                        violations.append((py_path.name, node.lineno, "chrt executable call"))

        self.assertEqual(
            violations,
            [],
            f"Found forbidden real-time scheduling API calls/references in precompile: {violations}",
        )

    def test_b_runtime_scheduling_policy_check(self):
        """Part B.2: Runtime check rejects real-time policies (FIFO=1, RR=2, DEADLINE=6) and accepts fair (0, 3, 5)."""
        with patch("os.sched_getscheduler", create=True) as mock_sched:
            # Fair classes
            mock_sched.return_value = 0  # SCHED_OTHER
            self.assertTrue(check_process_scheduling(1234))

            mock_sched.return_value = 3  # SCHED_BATCH
            self.assertTrue(check_process_scheduling(1234))

            mock_sched.return_value = 5  # SCHED_IDLE
            self.assertTrue(check_process_scheduling(1234))

            # Forbidden real-time classes
            mock_sched.return_value = 1  # SCHED_FIFO
            self.assertFalse(check_process_scheduling(1234))

            mock_sched.return_value = 2  # SCHED_RR
            self.assertFalse(check_process_scheduling(1234))

            mock_sched.return_value = 6  # SCHED_DEADLINE
            self.assertFalse(check_process_scheduling(1234))

    def test_c_weight_ceiling_and_clamping(self):
        """Part B.3: Weight cap validation: ceiling is 1000, clamps out-of-range weights."""
        gov = SafetyGovernor(max_weight=1500)  # Exceeds 1000 ceiling -> clamped to 1000
        self.assertEqual(gov.max_weight, 1000)

        gov2 = SafetyGovernor(max_weight=500)
        self.assertEqual(gov2.max_weight, 500)

        # Clamping
        self.assertEqual(gov2.clamp_weight(300), 300)
        self.assertEqual(gov2.clamp_weight(800), 500)  # Clamped to max_weight
        self.assertEqual(gov2.clamp_weight(0), 1)      # Clamped to min 1
        self.assertEqual(gov2.clamp_weight(-50), 1)

    def test_d_rolling_boost_budget_in_memory(self):
        """Part B.4: Rolling boost budget in-memory: exhausts and refills over time."""
        now = 1000.0
        gov = SafetyGovernor(hourly_budget_seconds=100.0, budget_interval_s=600.0)

        # Consume 40s -> passes
        self.assertTrue(gov.check_and_consume_budget(40.0, now=now))
        # Consume 50s -> passes (total 90s <= 100s)
        self.assertTrue(gov.check_and_consume_budget(50.0, now=now + 10.0))
        # Request 20s -> fails (90s + 20s = 110s > 100s)
        self.assertFalse(gov.check_and_consume_budget(20.0, now=now + 20.0))

        # Advance time by 610s (past the 600s rolling window) -> refills!
        now_future = now + 620.0
        self.assertTrue(gov.check_and_consume_budget(40.0, now=now_future))

    def test_e_rolling_boost_budget_persisted_in_store(self):
        """Part B.4: Budget persistence: uses DataStore reservations table across restarts."""
        now = 10000.0
        gov = SafetyGovernor(hourly_budget_seconds=300.0, budget_interval_s=3600.0)

        # Record completed reservation in store (duration 200s)
        self.store.record_reservation({
            "id": "res-1",
            "project_id": "demo",
            "weight": 500,
            "armed_at": now - 500.0,
            "window_lo_abs_s": now - 500.0,
            "window_hi_abs_s": now - 490.0,
            "claimed_at": now - 480.0,
            "closed_at": now - 280.0,  # 200s boost
            "group_name": "precompile-res-1",
            "final_state": "claimed",
        })

        # Used 200s of 300s budget. Another 80s should be permitted:
        self.assertTrue(gov.check_and_consume_budget(80.0, store=self.store, now=now))

        # Another 150s exceeds remaining 100s budget -> refused
        self.assertFalse(gov.check_and_consume_budget(150.0, store=self.store, now=now))

        # Advance time beyond 3600s -> window moves past res-1 -> refills!
        future_now = now + 4000.0
        self.assertTrue(gov.check_and_consume_budget(250.0, store=self.store, now=future_now))

    def test_f_circuit_breaker_tripping_and_cooldown(self):
        """Part B.5: Fault tolerance circuit breaker: trips on 5 consecutive errors, enters 300s cooldown."""
        sim_clock = 500.0
        gov = SafetyGovernor(
            circuit_breaker_threshold=5,
            circuit_breaker_cooldown=300.0,
            clock=lambda: sim_clock,
        )

        self.assertFalse(gov.is_circuit_breaker_active())

        # 4 consecutive failures -> not yet tripped
        for i in range(4):
            tripped = gov.record_actuation_failure(RuntimeError(f"Cgroup write failed {i}"))
            self.assertFalse(tripped)
            self.assertFalse(gov.is_circuit_breaker_active())

        # 1 success resets counter
        gov.record_actuation_success()
        self.assertEqual(gov.consecutive_actuation_errors, 0)

        # 5 consecutive failures -> trips!
        for i in range(4):
            gov.record_actuation_failure(RuntimeError(f"Cgroup write error {i}"))
            self.assertFalse(gov.is_circuit_breaker_active())

        tripped = gov.record_actuation_failure(RuntimeError("5th error"))
        self.assertTrue(tripped)
        self.assertTrue(gov.is_circuit_breaker_active())

        # During cooldown (sim_clock < 500.0 + 300.0 = 800.0), circuit breaker remains active
        sim_clock = 750.0
        self.assertTrue(gov.is_circuit_breaker_active())

        # When cooldown elapses (sim_clock = 801.0), circuit breaker resets to attempt recovery
        sim_clock = 801.0
        self.assertFalse(gov.is_circuit_breaker_active())
        self.assertEqual(gov.consecutive_actuation_errors, 0)

    def test_g_kill_switch_pause_and_resume(self):
        """Part B.6: Kill switch creates and unlinks pause file."""
        pause_file = self.temp_path / "precompile.pause"
        gov = SafetyGovernor(pause_file=pause_file)

        self.assertFalse(gov.is_paused())

        # Engage kill switch
        created = gov.pause()
        self.assertTrue(created.exists())
        self.assertTrue(gov.is_paused())

        # Disengage kill switch
        gov.resume()
        self.assertFalse(gov.is_paused())
        self.assertFalse(pause_file.exists())

    def test_h_privilege_and_cgroup_degradation_reasons(self):
        """Part B.7: Privilege and controller checks return specific degradation reasons."""
        fake_cgroup = self.temp_path / "sys_cgroup"
        fake_cgroup.mkdir(parents=True)
        gov = SafetyGovernor(cgroup_root=fake_cgroup / "precompile")

        # 1. Non-root degradation
        with patch("os.geteuid", return_value=1000):
            can_actuate = gov.check_privileges()
            self.assertFalse(can_actuate)
            self.assertEqual(gov.last_degradation_reason, "not_root")

        # 2. Cgroup v1 degradation (controllers file missing)
        with patch("os.geteuid", return_value=0), patch(
            "pathlib.Path.exists", autospec=True
        ) as mock_exists:
            # When checking /sys/fs/cgroup/cgroup.controllers, return False
            def exists_side_effect(path_obj):
                if str(path_obj) == "/sys/fs/cgroup/cgroup.controllers":
                    return False
                return True

            mock_exists.side_effect = exists_side_effect
            can_actuate = gov.check_privileges()
            self.assertFalse(can_actuate)
            self.assertEqual(gov.last_degradation_reason, "cgroup_v1")

        # 3. CPU controller missing degradation
        with patch("os.geteuid", return_value=0), patch(
            "pathlib.Path.exists", return_value=True
        ), patch("pathlib.Path.read_text", return_value="memory io pids"):
            can_actuate = gov.check_privileges()
            self.assertFalse(can_actuate)
            self.assertEqual(gov.last_degradation_reason, "no_cpu_controller")


if __name__ == "__main__":
    unittest.main()
