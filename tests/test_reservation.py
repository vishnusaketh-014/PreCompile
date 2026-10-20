"""Unit tests for Anticipatory Reservation Management (precompile/reservation.py).

Tests:
    a. All legal state transitions:
       - IDLE -> ARMED -> ACTIVE -> CLAIMED -> CLOSED
       - ARMED -> CLAIMED (early match)
       - ARMED -> EXPIRED
       - ACTIVE -> EXPIRED
       - ANY -> CANCELLED
    b. Newer save resets window; 1 reservation per project; max_reservations enforced.
    c. Window already open at arm time starts ACTIVE.
    d. Claim moves winner plus existing descendants; rejects wrong project/already claimed.
    e. Concurrency: 8 threads calling try_claim with atomic_claim=True over 200 rounds -> exactly 1 winner;
       with atomic_claim=False and interleaving hook -> deterministically 2 winners.
    f. Weight clamped; max lifetime reverts weight to 100 and closes; groups removed on terminal states.
    g. Not-root fallback: available is False, arm() returns None, no exception.
    j. Migration v3 and reservation_summary.
"""

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Dict, List, Optional

from precompile.cgroup import CgroupController, FakeCgroupBackend
from precompile.reservation import (
    Reservation,
    ReservationManager,
    ReservationRecord,
    ReservationState,
)
from precompile.store import DataStore
from precompile.watcher import ProcessInfo


class FakeClock:
    """Deterministic monotonic clock for simulation tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.current = start

    def __call__(self) -> float:
        return self.current

    def advance(self, delta_s: float) -> None:
        self.current += delta_s


def _make_proc(
    pid: int,
    ppid: int = 1,
    t_start: float = 1000.0,
    starttime_ticks: Optional[int] = None,
) -> ProcessInfo:
    return ProcessInfo(
        pid=pid,
        ppid=ppid,
        starttime_ticks=starttime_ticks if starttime_ticks is not None else int(t_start * 100),
        exe=Path("/usr/bin/make"),
        cwd=Path("/home/dev/proj"),
        cmdline=["make"],
        t_seen_monotonic=t_start,
        t_start_monotonic=t_start,
        t_start_source="test",
    )


class TestReservationManager(unittest.TestCase):
    """Test suite for ReservationManager state machine and invariants."""

    def setUp(self):
        self.clock = FakeClock(1000.0)
        self.backend = FakeCgroupBackend(weight_cap=500, default_weight=500)
        self.pid_ticks: Dict[int, int] = {}
        self.mgr = ReservationManager(
            cgroup_backend=self.backend,
            clock=self.clock,
            max_reservations=4,
            atomic_claim=True,
            starttime_reader=lambda pid: self.pid_ticks.get(pid, None),
        )

    def test_a1_standard_lifecycle(self):
        """Transition path: ARMED -> ACTIVE -> CLAIMED -> CLOSED."""
        rec = self.mgr.arm("proj1", 1, "default", 1000.0, 1.0, 3.0, 0.5)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.state, ReservationState.ARMED)
        self.assertIn(rec.group_name, self.backend.groups)

        # 0.5s later: still in ARMED
        self.clock.advance(0.5)
        self.mgr.tick()
        self.assertEqual(rec.state, ReservationState.ARMED)

        # 1.0s later (at 1001.0): window opens -> ACTIVE
        self.clock.advance(0.6)
        self.mgr.tick()
        self.assertEqual(rec.state, ReservationState.ACTIVE)

        # Claim while active
        proc = _make_proc(101, t_start=1001.2)
        success = self.mgr.try_claim("proj1", proc)
        self.assertTrue(success)
        self.assertEqual(rec.state, ReservationState.CLAIMED)
        self.assertEqual(rec.claimed_pid, 101)
        self.assertIn(101, self.backend.list_procs(rec.group_name))

        # Process exits -> group empty -> CLOSED
        self.backend.groups[rec.group_name]["pids"].clear()
        self.backend.groups[rec.group_name]["populated"] = False
        self.mgr.tick()
        self.assertEqual(rec.state, ReservationState.CLOSED)
        # Group must be removed on terminal state
        self.assertNotIn(rec.group_name, self.backend.groups)

    def test_a2_early_match(self):
        """Transition path: ARMED -> CLAIMED directly (early match before window opens)."""
        rec = self.mgr.arm("proj1", 1, "default", 1000.0, 1.0, 3.0, 0.5)
        self.assertEqual(rec.state, ReservationState.ARMED)

        # Process appears at 1000.4 (before window_lo 1001.0)
        self.clock.advance(0.4)
        proc = _make_proc(202, t_start=1000.4)
        success = self.mgr.try_claim("proj1", proc)
        self.assertTrue(success)
        self.assertEqual(rec.state, ReservationState.CLAIMED)
        self.assertEqual(rec.claimed_pid, 202)

    def test_a3_armed_to_expired(self):
        """Transition path: ARMED -> EXPIRED when window opens and closes with no match."""
        rec = self.mgr.arm("proj1", 1, "default", 1000.0, 1.0, 3.0, 0.5)
        self.assertEqual(rec.state, ReservationState.ARMED)

        # Advance past window_hi (1003.0)
        self.clock.advance(3.5)
        self.mgr.tick()
        self.assertEqual(rec.state, ReservationState.EXPIRED)
        self.assertNotIn(rec.group_name, self.backend.groups)

    def test_a4_active_to_expired(self):
        """Transition path: ACTIVE -> EXPIRED when window closes with no match."""
        rec = self.mgr.arm("proj1", 1, "default", 1000.0, 1.0, 3.0, 0.5)
        self.clock.advance(1.5)
        self.mgr.tick()
        self.assertEqual(rec.state, ReservationState.ACTIVE)

        # Advance past window_hi
        self.clock.advance(2.0)
        self.mgr.tick()
        self.assertEqual(rec.state, ReservationState.EXPIRED)
        self.assertNotIn(rec.group_name, self.backend.groups)

    def test_a5_any_to_cancelled(self):
        """Transition path: cancel() moves any active state to CANCELLED and removes cgroup."""
        rec = self.mgr.arm("proj1", 1, "default", 1000.0, 1.0, 3.0, 0.5)
        grp_name = rec.group_name
        self.assertIn(grp_name, self.backend.groups)

        success = self.mgr.cancel(rec.id, reason="user_stopped")
        self.assertTrue(success)
        self.assertEqual(rec.state, ReservationState.CANCELLED)
        self.assertNotIn(grp_name, self.backend.groups)

    def test_b_newer_save_resets_window_and_limits(self):
        """Requirement b: Newer save resets window; at most 1 reservation per project; max_reservations enforced."""
        rec1 = self.mgr.arm("proj1", 1, "default", 1000.0, 1.0, 3.0, 0.5)
        orig_lo = rec1.window_lo_abs
        orig_hi = rec1.window_hi_abs

        # Second save at 1000.8 for proj1
        self.clock.advance(0.8)
        rec2 = self.mgr.arm("proj1", 2, "default", 1000.8, 1.0, 3.0, 0.5)
        # Should be the SAME reservation instance with updated window
        self.assertEqual(rec1.id, rec2.id)
        self.assertEqual(rec1.window_lo_abs, 1001.8)
        self.assertEqual(rec1.window_hi_abs, 1003.8)
        self.assertGreater(rec1.window_lo_abs, orig_lo)

        # Enforce max_reservations (limit=4)
        rec_b = self.mgr.arm("proj2", 3, "default", 1000.8, 1.0, 3.0)
        rec_c = self.mgr.arm("proj3", 4, "default", 1000.8, 1.0, 3.0)
        rec_d = self.mgr.arm("proj4", 5, "default", 1000.8, 1.0, 3.0)
        self.assertIsNotNone(rec_b)
        self.assertIsNotNone(rec_c)
        self.assertIsNotNone(rec_d)

        # 5th concurrent project must be rejected
        rec_e = self.mgr.arm("proj5", 6, "default", 1000.8, 1.0, 3.0)
        self.assertIsNone(rec_e)

    def test_c_window_already_open_starts_active(self):
        """Requirement c: If window is already open at arm time, reservation starts directly in ACTIVE."""
        # Save occurred at 1000.0, window_lo is 0.5s.
        # But arm() is called at 1001.0 (now - t_save = 1.0s >= window_lo).
        self.clock.advance(1.0)
        rec = self.mgr.arm("proj1", 1, "default", 1000.0, 0.5, 2.5)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.state, ReservationState.ACTIVE)

    def test_d_claim_moves_winner_and_descendants_checks_rules(self):
        """Requirement d: Claim moves winner and descendants; wrong project or already claimed rejected."""
        rec = self.mgr.arm("proj1", 1, "default", 1000.0, 1.0, 3.0)
        self.clock.advance(1.5)
        self.mgr.tick()

        # Build process table with parent 300 -> child 301, 302
        p_parent = _make_proc(300, ppid=1, t_start=1001.5)
        p_child1 = _make_proc(301, ppid=300, t_start=1001.6)
        p_child2 = _make_proc(302, ppid=300, t_start=1001.7)
        proc_table = {300: p_parent, 301: p_child1, 302: p_child2}

        # Claiming for non-existent project fails
        self.assertFalse(self.mgr.try_claim("proj_wrong", p_parent, proc_table))

        # Claim for proj1 succeeds
        self.assertTrue(self.mgr.try_claim("proj1", p_parent, proc_table))
        self.assertEqual(rec.state, ReservationState.CLAIMED)

        # Verify all descendants were moved
        procs_in_cg = set(self.backend.list_procs(rec.group_name))
        self.assertEqual(procs_in_cg, {300, 301, 302})

        # Process cannot be re-claimed elsewhere
        rec2 = self.mgr.arm("proj2", 2, "default", 1001.5, 0.1, 5.0)
        self.assertFalse(self.mgr.try_claim("proj2", p_parent, proc_table))

    def test_e_concurrency_atomic_vs_non_atomic(self):
        """Requirement e: Concurrency test over 200 rounds.

        With atomic_claim=True: 8 threads race on try_claim -> exactly 1 winner every round.
        With atomic_claim=False and test hook: deterministically shows two winners can occur.
        """
        # Part 1: atomic_claim=True (200 rounds)
        for round_idx in range(200):
            mgr = ReservationManager(
                cgroup_backend=FakeCgroupBackend(),
                clock=self.clock,
                atomic_claim=True,
                starttime_reader=lambda pid: int(1000.0 * 100),
            )
            rec = mgr.arm("proj1", round_idx, "default", 1000.0, 0.0, 5.0)
            mgr.tick()  # active

            results = []

            def worker(pid):
                p = _make_proc(pid, t_start=1000.0)
                res = mgr.try_claim("proj1", p)
                results.append(res)

            threads = [threading.Thread(target=worker, args=(500 + i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            winners = sum(1 for r in results if r is True)
            self.assertEqual(winners, 1, f"Round {round_idx}: expected exactly 1 winner, got {winners}")

        # Part 2: atomic_claim=False with interleaving hook
        hook_called = threading.Event()
        proceed = threading.Event()

        def interleaving_hook():
            hook_called.set()
            proceed.wait(timeout=1.0)

        non_atomic_mgr = ReservationManager(
            cgroup_backend=FakeCgroupBackend(),
            clock=self.clock,
            atomic_claim=False,
            claim_interleave_hook=interleaving_hook,
            starttime_reader=lambda pid: int(1000.0 * 100),
        )
        rec = non_atomic_mgr.arm("proj1", 999, "default", 1000.0, 0.0, 5.0)
        non_atomic_mgr.tick()

        res_thread1 = []
        res_thread2 = []

        def worker1():
            p1 = _make_proc(501, t_start=1000.0)
            res_thread1.append(non_atomic_mgr.try_claim("proj1", p1))

        def worker2():
            # Wait until thread 1 checked flag and hit hook
            hook_called.wait(timeout=1.0)
            p2 = _make_proc(502, t_start=1000.0)
            res_thread2.append(non_atomic_mgr.try_claim("proj1", p2))
            proceed.set()

        t1 = threading.Thread(target=worker1)
        t2 = threading.Thread(target=worker2)
        t1.start()
        t2.start()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        # Both workers succeeded because claim was non-atomic!
        self.assertTrue(res_thread1[0])
        self.assertTrue(res_thread2[0])

    def test_f_weight_cap_and_max_lifetime_revert(self):
        """Requirement f: Weight clamped to cap; max lifetime reverts weight to 100 and closes group."""
        # Arm with high weight -> clamped to 500
        rec = self.mgr.arm("proj1", 1, "default", 1000.0, 0.0, 5.0, median_build_dur=1.0, weight=999)
        self.assertEqual(rec.weight, 999)  # record stores requested weight
        grp_w = self.backend.groups[rec.group_name]["weight"]
        self.assertEqual(grp_w, 500)  # backend clamped to cap

        # Lifetime for median 1.0s is clamp(5 * 1.0, 5.0, 120.0) = 5.0s
        self.assertEqual(rec.max_lifetime_s, 5.0)

        # Claim
        proc = _make_proc(601, t_start=1000.0)
        self.mgr.try_claim("proj1", proc)
        self.assertEqual(rec.state, ReservationState.CLAIMED)

        # Advance past max_lifetime_s (5.5s) while process still running
        self.clock.advance(5.5)
        self.mgr.tick()

        # Should be CLOSED and group released
        self.assertEqual(rec.state, ReservationState.CLOSED)
        self.assertNotIn(rec.group_name, self.backend.groups)

    def test_g_not_root_fallback(self):
        """Requirement g: When not root or cgroup unwritable, available is False and arm() returns None without exception."""
        # Point to unwritable directory
        mgr_unwritable = ReservationManager(
            cgroup_backend=CgroupController(base_cgroup_path="/unwritable/nonexistent/cgroup"),
            clock=self.clock,
        )
        self.assertFalse(mgr_unwritable.available)
        result = mgr_unwritable.arm("proj1", 1, "default", 1000.0, 1.0, 3.0)
        self.assertIsNone(result)

    def test_j_migration_v3_and_reservation_summary(self):
        """Requirement j: Database migration v3 from v1/v2 databases and reservation_summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_migration.db"

            # Create a v1 database directly with SQLite
            conn = sqlite3.connect(str(db_path))
            conn.executescript(
                """
                CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
                INSERT INTO schema_version (version, applied_at) VALUES (1, 1000.0);
                CREATE TABLE projects (id TEXT PRIMARY KEY, root_path TEXT NOT NULL, tool_profile_name TEXT NOT NULL, enabled INTEGER NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, file_class TEXT, tool_profile TEXT, t_save_wall REAL, window_lo REAL, window_hi REAL, armed INTEGER, holdout INTEGER, outcome TEXT, observed_delay_s REAL, created_at REAL);
                INSERT INTO projects VALUES ('demo', '/tmp/demo', 'default', 1, 1000.0);
                INSERT INTO predictions (id, project_id, file_class, tool_profile, t_save_wall, window_lo, window_hi, armed, holdout, outcome, observed_delay_s, created_at)
                    VALUES (1, 'demo', 'source', 'default', 1000.0, 1.0, 2.0, 0, 0, 'hit', 1.5, 1000.0);
                """
            )
            conn.close()

            # Open with DataStore -> runs migrate() through latest version (v3+)
            store = DataStore(db_path=db_path)
            cur = store._conn.execute("SELECT MAX(version) FROM schema_version")
            self.assertGreaterEqual(cur.fetchone()[0], 3)

            # Check reservations table exists
            cur = store._conn.execute("PRAGMA table_info(reservations)")
            cols = {row[1] for row in cur.fetchall()}
            self.assertIn("id", cols)
            self.assertIn("project_id", cols)
            self.assertIn("final_state", cols)
            self.assertIn("claim_latency_s", cols)

            # Record reservations
            store.record_reservation({
                "id": "res-1",
                "project_id": "demo",
                "prediction_id": 1,
                "final_state": "CLOSED",
                "weight": 500,
                "armed_at": 1000.0,
                "window_lo_abs_s": 1001.0,
                "window_hi_abs_s": 1003.0,
                "claimed_at": 1001.2,
                "claim_latency_s": 0.020,
                "claimed_pid": 800,
                "closed_at": 1001.8,
                "group_name": "precompile-res-1",
                "reason": "would_arm",
            })
            store.record_reservation({
                "id": "res-2",
                "project_id": "demo",
                "prediction_id": None,
                "final_state": "EXPIRED",
                "weight": 500,
                "armed_at": 1005.0,
                "window_lo_abs_s": 1006.0,
                "window_hi_abs_s": 1008.0,
                "claimed_at": None,
                "claim_latency_s": None,
                "claimed_pid": None,
                "closed_at": 1008.1,
                "group_name": "precompile-res-2",
                "reason": "expired",
            })

            # Check summary
            summary = store.reservation_summary("demo")
            self.assertEqual(summary["total"], 2)
            self.assertEqual(summary["counts"]["CLOSED"], 1)
            self.assertEqual(summary["counts"]["EXPIRED"], 1)
            self.assertAlmostEqual(summary["mean_claim_latency_s"], 0.020, places=3)

            # Check list_reservations
            res_list = store.list_reservations("demo")
            self.assertEqual(len(res_list), 2)
            store.close()

    def test_claim_tick_race_move_done(self):
        """Part A.3: Reservation must not close for empty group until move_done has completed."""
        def tick_during_move():
            # Call tick() between state set to CLAIMED and move complete
            transitioned = self.mgr.tick()
            # Must NOT transition to CLOSED while move is pending
            self.assertEqual(len(transitioned), 0)

        mgr = ReservationManager(
            cgroup_backend=FakeCgroupBackend(),
            clock=self.clock,
            move_interleave_hook=tick_during_move,
        )
        rec = mgr.arm("proj1", 1, "default", 1000.0, 0.0, 5.0)
        mgr.tick()  # active

        p = _make_proc(999, t_start=1000.0)
        claimed = mgr.try_claim("proj1", p)
        self.assertTrue(claimed)
        self.assertEqual(rec.state, ReservationState.CLAIMED)
        self.assertTrue(rec.move_done)

    def test_pid_reuse_guard(self):
        """Part D.j: A pid whose starttime in proc changed is NOT moved into cgroup."""
        backend = FakeCgroupBackend()

        # 1. Test via injected starttime_reader (independent of /proc)
        fake_ticks_table = {1234: 99999}
        mgr_injected = ReservationManager(
            cgroup_backend=backend,
            clock=self.clock,
            starttime_reader=lambda pid: fake_ticks_table.get(pid),
        )
        rec_inj = mgr_injected.arm("proj1", 1, "default", 1000.0, 0.0, 5.0)
        mgr_injected.tick()
        p_inj = _make_proc(1234, starttime_ticks=88888, t_start=1000.0)
        claimed_inj = mgr_injected.try_claim("proj1", p_inj)
        self.assertFalse(claimed_inj, "Claim must be refused when starttime_reader ticks mismatch")
        self.assertEqual(backend.list_procs(rec_inj.group_name), [])

        # 2. Test via filesystem /proc/<pid>/stat parsing
        with tempfile.TemporaryDirectory() as fake_proc_dir:
            proc_p = Path(fake_proc_dir)
            mgr = ReservationManager(
                cgroup_backend=backend,
                clock=self.clock,
                proc_path=proc_p,
            )
            rec = mgr.arm("proj1", 2, "default", 1000.0, 0.0, 5.0)
            mgr.tick()

            # Create fake /proc/1234/stat with starttime 99999
            stat_dir = proc_p / "1234"
            stat_dir.mkdir(parents=True, exist_ok=True)
            stat_file = stat_dir / "stat"
            # stat format: pid (comm) state ppid ... field22=99999
            fields = ["0"] * 20
            fields[1] = "1"  # ppid
            fields[19] = "99999"  # starttime
            stat_file.write_text(f"1234 (fake) S {' '.join(fields)}\n")

            # ProcessInfo recorded with old starttime 88888 (different from proc)
            p = _make_proc(1234, starttime_ticks=88888, t_start=1000.0)

            # Claim must be refused due to starttime mismatch
            claimed = mgr.try_claim("proj1", p)
            self.assertFalse(claimed)
            # Backend should have zero processes moved
            self.assertEqual(backend.list_procs(rec.group_name), [])


    def test_try_claim_latency_under_10ms(self):
        """Part A.1(a): try_claim returns in under 10 ms even with a 0.3s sweep window."""
        def dummy_enumerator(root_pid, root_starttime):
            time.sleep(0.05)  # Simulate slow /proc inspection
            return set()

        backend = FakeCgroupBackend()
        mgr = ReservationManager(
            cgroup_backend=backend,
            clock=time.monotonic,
            sweep_window_s=0.3,
            proc_enumerator=dummy_enumerator,
        )
        rec = mgr.arm("proj1", 1, "default", time.monotonic(), 0.0, 5.0)
        mgr.tick()

        proc = _make_proc(8888, t_start=time.monotonic())
        t0 = time.perf_counter()
        claimed = mgr.try_claim("proj1", proc)
        t_elapsed = time.perf_counter() - t0

        self.assertTrue(claimed)
        self.assertLess(t_elapsed, 0.010, f"try_claim took {t_elapsed*1000:.2f}ms, expected < 10ms")
        # Cleanup
        mgr.cancel_all()

    def test_watcher_scans_during_sweep(self):
        """Part A.1(b): ProcessWatcher scan count keeps increasing during a sweep."""
        from precompile.watcher import ProcessWatcher

        backend = FakeCgroupBackend()
        mgr = ReservationManager(
            cgroup_backend=backend,
            clock=time.monotonic,
            sweep_window_s=0.25,
            proc_enumerator=lambda r, s: set(),
        )
        rec = mgr.arm("proj1", 1, "default", time.monotonic(), 0.0, 5.0)
        mgr.tick()

        # Create real watcher with fast interval
        watcher = ProcessWatcher(
            poll_interval_normal_ms=20,
            poll_interval_intensified_ms=20,
        )
        watcher.start()
        try:
            time.sleep(0.05)
            initial_scans = watcher.get_stats().scans_performed

            proc = _make_proc(7777, t_start=time.monotonic())
            mgr.try_claim("proj1", proc)

            # Sleep during active sweep window
            time.sleep(0.15)
            later_scans = watcher.get_stats().scans_performed
            self.assertGreater(
                later_scans,
                initial_scans + 2,
                "Watcher scan count must keep increasing while background sweep is active",
            )
        finally:
            watcher.stop()
            mgr.cancel_all()

    def test_tick_cannot_close_before_sweep_finishes(self):
        """Part A.1(c): tick() cannot close a reservation before the sweep finishes (move_done semantics)."""
        backend = FakeCgroupBackend()
        sweep_gate = threading.Event()

        def blocked_enumerator(root_pid, root_starttime):
            sweep_gate.wait(timeout=2.0)
            return set()

        mgr = ReservationManager(
            cgroup_backend=backend,
            clock=time.monotonic,
            sweep_window_s=0.2,
            proc_enumerator=blocked_enumerator,
        )
        rec = mgr.arm("proj1", 1, "default", time.monotonic(), 0.0, 5.0)
        mgr.tick()

        proc = _make_proc(6666, t_start=time.monotonic())
        claimed = mgr.try_claim("proj1", proc)
        self.assertTrue(claimed)
        self.assertFalse(rec.move_done)

        # Force cgroup to appear empty
        backend.groups[rec.group_name]["pids"].clear()
        backend.groups[rec.group_name]["populated"] = False

        # Call tick while sweep is still in progress
        transitioned = mgr.tick()
        self.assertEqual(len(transitioned), 0)
        self.assertEqual(rec.state, ReservationState.CLAIMED)
        self.assertFalse(rec.move_done)

        # Unblock sweep and wait for it to complete
        sweep_gate.set()
        time.sleep(0.25)
        self.assertTrue(rec.move_done)

        # Ensure group is empty (sweep pass 1 moves root PID if not prevented)
        backend.groups[rec.group_name]["pids"].clear()
        backend.groups[rec.group_name]["populated"] = False

        # Now tick() should see empty group and transition to CLOSED
        transitioned = mgr.tick()
        self.assertEqual(len(transitioned), 1)
        self.assertEqual(rec.state, ReservationState.CLOSED)
        mgr.cancel_all()

    def test_fixpoint_loop_mock_enumerator(self):
        """Part A.3: Unit test fixpoint loop with injectable mock /proc enumerator."""
        backend = FakeCgroupBackend()
        calls = 0

        def phased_enumerator(root_pid, root_starttime):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {5001}  # First pass discovers PID 5001
            elif calls == 2:
                return {5001, 5002}  # Second pass discovers 5002
            return {5001, 5002}

        mgr = ReservationManager(
            cgroup_backend=backend,
            clock=time.monotonic,
            sweep_window_s=0.1,
            proc_enumerator=phased_enumerator,
        )
        rec = mgr.arm("proj1", 1, "default", time.monotonic(), 0.0, 5.0)
        mgr.tick()

        proc = _make_proc(5000, t_start=time.monotonic())
        claimed = mgr.try_claim("proj1", proc)
        self.assertTrue(claimed)

        # Wait for sweep to finish
        time.sleep(0.15)
        self.assertTrue(rec.move_done)

        # Verify all discovered PIDs were moved into backend
        procs_in_group = backend.list_procs(rec.group_name)
        self.assertIn(5000, procs_in_group)
        self.assertIn(5001, procs_in_group)
        self.assertIn(5002, procs_in_group)
        mgr.cancel_all()


if __name__ == "__main__":
    unittest.main()

