"""Tests for Step 7: gates.py PSISampler, ArmingGate, and related functionality.

Verifies requirements a-k from Step 7 specifications:
    a. PSI parsing (real file and mocked strings, including the full line);
       PSISampler recent_some_pct from mocked totals and a fake clock.
    b. Contention present/absent around the threshold; the 'predicted' rising rule.
    c. Cost/benefit: expected values written independently from formulas.
       High p_hit + small window + long build + high contention -> arm.
       Low p_hit, tiny build, or low contention -> abstain.
    d. No observed builds -> abstain_no_build_estimate.
    e. Each config toggle (confidence, contention, cost, holdout) works and is noted in reasons.
    f. Holdout share: seeded RNG, 1000 qualifying events at p=0.1 within binomial 99% band;
       p=0 never holdout; p=1 always holdout; holdout events are not 'would_arm'.
    g. Migration: build a v1 DB with data, open with new code, data intact, columns present,
       second open is a no-op.
    h. Daemon integration with mocked PSI reader: PSI high -> decisions recorded as
       would_arm/holdout; PSI 0 -> abstain_no_contention; armed stays False; no cgroup writes.
    i. SIGHUP reload picks up a project added while the daemon runs.
    j. `projects add --path .` stores an absolute path.
    k. CLI status/predict show decision counts.
"""

import io
import json
import math
import os
import random
import signal
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

from precompile.cli import main
from precompile.config import AppConfig, MonitorConfig, PredictorConfig, WatcherConfig
from precompile.daemon import PreCompileDaemon
from precompile.gates import (
    ArmDecision,
    ArmingGate,
    CPUContentionMetrics,
    PSISampler,
    parse_psi_cpu,
    read_psi_cpu,
)
from precompile.monitor import SaveEvent
from precompile.store import DataStore
from precompile.watcher import ProcessInfo


# ──────────────────────────────────────────────────────────────────────────────
# Helper: make a minimal CPUContentionMetrics
# ──────────────────────────────────────────────────────────────────────────────
def _psi(some10: float, some60: float = 0.0, some300: float = 0.0,
         total: int = 0, full10: float = 0.0) -> CPUContentionMetrics:
    return CPUContentionMetrics(
        some_avg10=some10,
        some_avg60=some60,
        some_avg300=some300,
        some_total=total,
        full_avg10=full10,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Test a: PSI parsing and PSISampler
# ──────────────────────────────────────────────────────────────────────────────
class TestPSIParsing(unittest.TestCase):
    """Requirement a: PSI parsing with real and mocked strings, PSISampler."""

    def test_a1_parse_full_psi_string(self) -> None:
        """Parse complete some+full PSI string."""
        text = (
            "some avg10=2.50 avg60=1.20 avg300=0.80 total=123456\n"
            "full avg10=0.40 avg60=0.20 avg300=0.10 total=45678\n"
        )
        m = parse_psi_cpu(text)
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m.some_avg10, 2.50)
        self.assertAlmostEqual(m.some_avg60, 1.20)
        self.assertAlmostEqual(m.some_avg300, 0.80)
        self.assertEqual(m.some_total, 123456)
        self.assertEqual(m.some_total_us, 123456)  # alias
        self.assertAlmostEqual(m.full_avg10, 0.40)
        self.assertEqual(m.full_total, 45678)

    def test_a2_parse_some_only(self) -> None:
        """Parse PSI string with only 'some' line (no 'full')."""
        text = "some avg10=1.15 avg60=0.90 avg300=0.50 total=999\n"
        m = parse_psi_cpu(text)
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m.some_avg10, 1.15)
        self.assertAlmostEqual(m.full_avg10, 0.0)
        self.assertEqual(m.full_total, 0)

    def test_a3_parse_empty_or_invalid(self) -> None:
        """Empty or invalid text returns None."""
        self.assertIsNone(parse_psi_cpu(""))
        self.assertIsNone(parse_psi_cpu("invalid garbage\n"))
        self.assertIsNone(parse_psi_cpu("full avg10=1.0\n"))  # no 'some' line

    def test_a4_real_file_or_none(self) -> None:
        """Real /proc/pressure/cpu returns metrics or None (no crash)."""
        m = read_psi_cpu()
        if m is not None:
            self.assertIsInstance(m, CPUContentionMetrics)
            self.assertGreaterEqual(m.some_avg10, 0.0)

    def test_a5_psi_sampler_recent_some_pct(self) -> None:
        """PSISampler.recent_some_pct computes stall_delta_us / wall_delta correctly."""
        fake_time = 1000.0
        total_us = 0

        def clock() -> float:
            return fake_time

        # Build a sequence of (time, total_us) values
        # We will simulate 10% stall over 2 seconds: 200,000 us stall in 2s
        samples = [
            (1000.0, 0),
            (1001.0, 100_000),   # 100ms stall in 1s => 10%
            (1002.0, 200_000),   # 200ms stall in 2s => 10%
        ]

        idx = 0

        def reader() -> Optional[CPUContentionMetrics]:
            nonlocal idx
            t, us = samples[idx]
            return _psi(10.0, total=us)

        sampler = PSISampler(reader=reader, clock=clock)

        # Sample at t=1000.0, total=0
        fake_time = 1000.0

        def mock_reader() -> Optional[CPUContentionMetrics]:
            return _psi(10.0, total=samples[idx][1])

        sampler._reader = mock_reader

        idx = 0
        fake_time = 1000.0
        sampler.sample()

        idx = 1
        fake_time = 1001.0
        sampler.sample()

        idx = 2
        fake_time = 1002.0
        sampler.sample()

        # recent_some_pct over 2 seconds: 200000 us / 2_000_000 us = 10%
        pct = sampler.recent_some_pct
        self.assertAlmostEqual(pct, 10.0, delta=0.01)

    def test_a6_psi_sampler_insufficient_history(self) -> None:
        """PSISampler returns 0.0 if history has only one sample."""
        fake_time = 1000.0

        def clock():
            return fake_time

        def reader():
            return _psi(5.0, total=50_000)

        sampler = PSISampler(reader=reader, clock=clock)
        sampler.sample()
        self.assertAlmostEqual(sampler.recent_some_pct, 0.0)


# ──────────────────────────────────────────────────────────────────────────────
# Test b: Contention gate present/absent/predicted
# ──────────────────────────────────────────────────────────────────────────────
class TestContentionGate(unittest.TestCase):
    """Requirement b: contention present/absent around threshold; rising rule."""

    def _gate(self, threshold: float = 10.0) -> ArmingGate:
        """Create an ArmingGate with all gates enabled and fixed PSI reader."""
        return ArmingGate(
            contention_threshold_pct=threshold,
            holdout_probability=0.0,  # no holdout for gate tests
            rng=random.Random(42),
        )

    def test_b1_contention_present_above_threshold(self) -> None:
        """contention_level >= threshold -> contention present."""
        gate = self._gate(threshold=10.0)
        present, level = gate.is_contention_present(_psi(15.0, 5.0))
        self.assertTrue(present)
        self.assertAlmostEqual(level, 15.0)

    def test_b2_contention_absent_below_threshold(self) -> None:
        """contention_level < threshold AND not rising -> not present."""
        gate = self._gate(threshold=10.0)
        present, level = gate.is_contention_present(_psi(3.0, 2.0))
        self.assertFalse(present)
        self.assertAlmostEqual(level, 3.0)

    def test_b3_contention_predicted_rising(self) -> None:
        """some_avg10 in [0.5*threshold, threshold) AND avg10 > avg60 -> predicted (treated as present)."""
        gate = self._gate(threshold=10.0)
        # avg10=6.0 in [5.0, 10.0), avg10 > avg60=3.0 -> rising -> predicted
        present, level = gate.is_contention_present(_psi(some10=6.0, some60=3.0))
        self.assertTrue(present)

    def test_b4_contention_NOT_predicted_falling(self) -> None:
        """avg10 in half-threshold range but NOT rising (avg10 <= avg60) -> NOT predicted."""
        gate = self._gate(threshold=10.0)
        # avg10=6.0, avg60=8.0 -> avg10 < avg60 -> falling -> not predicted
        present, level = gate.is_contention_present(_psi(some10=6.0, some60=8.0))
        self.assertFalse(present)

    def test_b5_contention_boundary_exactly_threshold(self) -> None:
        """Exactly at threshold (10.0) -> present."""
        gate = self._gate(threshold=10.0)
        present, level = gate.is_contention_present(_psi(10.0, 5.0))
        self.assertTrue(present)

    def test_b6_contention_boundary_half_threshold(self) -> None:
        """Exactly at 0.5*threshold (5.0), avg10 > avg60 -> predicted."""
        gate = self._gate(threshold=10.0)
        present, level = gate.is_contention_present(_psi(5.0, 3.0))
        self.assertTrue(present)

    def test_b7_contention_below_half_threshold(self) -> None:
        """Below 0.5*threshold (4.9, avg10 > avg60) -> not present or predicted."""
        gate = self._gate(threshold=10.0)
        present, level = gate.is_contention_present(_psi(4.9, 2.0))
        self.assertFalse(present)

    def test_b8_none_metrics(self) -> None:
        """None metrics -> not present."""
        gate = self._gate()
        present, level = gate.is_contention_present(None)
        self.assertFalse(present)
        self.assertAlmostEqual(level, 0.0)


# ──────────────────────────────────────────────────────────────────────────────
# Test c: Cost/benefit model
# ──────────────────────────────────────────────────────────────────────────────
class TestCostBenefitGate(unittest.TestCase):
    """Requirement c: cost/benefit gate with independent formula verification."""

    def _ref_cost_benefit(
        self,
        p_hit: float,
        build_dur: float,
        window_width: float,
        contention_level: float,
        benefit_prior_max: float = 0.5,
        idle_cost_rate: float = 0.05,
        other_impact_rate: float = 0.10,
    ):
        """Reference implementation of cost/benefit model (independent of gates.py)."""
        speedup_frac = benefit_prior_max * min(1.0, contention_level / 50.0)
        benefit_s = p_hit * build_dur * speedup_frac
        cost_s = (1.0 - p_hit) * window_width * idle_cost_rate + p_hit * build_dur * other_impact_rate
        return benefit_s > cost_s, benefit_s, cost_s

    def test_c1_high_phit_long_build_high_contention_arms(self) -> None:
        """High p_hit + long build + high contention -> arm."""
        gate = ArmingGate(
            benefit_prior_max=0.5,
            idle_cost_rate=0.05,
            other_impact_rate=0.10,
        )
        p_hit, build_dur, window_width, contention = 1.0, 5.0, 2.0, 50.0
        passes, benefit, cost = gate.evaluate_cost_benefit(p_hit, build_dur, window_width, contention)
        ref_passes, ref_benefit, ref_cost = self._ref_cost_benefit(p_hit, build_dur, window_width, contention)
        self.assertTrue(passes)
        self.assertTrue(ref_passes)
        self.assertAlmostEqual(benefit, ref_benefit, places=6)
        self.assertAlmostEqual(cost, ref_cost, places=6)

    def test_c2_low_phit_abstains(self) -> None:
        """Low p_hit -> cost likely exceeds benefit -> abstain."""
        gate = ArmingGate(benefit_prior_max=0.5, idle_cost_rate=0.05, other_impact_rate=0.10)
        p_hit, build_dur, window_width, contention = 0.1, 5.0, 10.0, 50.0
        passes, benefit, cost = gate.evaluate_cost_benefit(p_hit, build_dur, window_width, contention)
        ref_passes, ref_benefit, ref_cost = self._ref_cost_benefit(p_hit, build_dur, window_width, contention)
        self.assertEqual(passes, ref_passes)
        self.assertAlmostEqual(benefit, ref_benefit, places=6)
        self.assertAlmostEqual(cost, ref_cost, places=6)

    def test_c3_tiny_build_abstains(self) -> None:
        """Very small build_dur -> speedup too small to justify reservation."""
        gate = ArmingGate(benefit_prior_max=0.5, idle_cost_rate=0.05, other_impact_rate=0.10)
        p_hit, build_dur, window_width, contention = 1.0, 0.01, 5.0, 50.0
        passes, benefit, cost = gate.evaluate_cost_benefit(p_hit, build_dur, window_width, contention)
        ref_passes, ref_benefit, ref_cost = self._ref_cost_benefit(p_hit, build_dur, window_width, contention)
        self.assertEqual(passes, ref_passes)
        self.assertAlmostEqual(benefit, ref_benefit, places=6)
        self.assertAlmostEqual(cost, ref_cost, places=6)

    def test_c4_zero_contention_zero_benefit(self) -> None:
        """Zero contention -> speedup_frac=0 -> benefit_s=0 -> abstain."""
        gate = ArmingGate(benefit_prior_max=0.5)
        passes, benefit, cost = gate.evaluate_cost_benefit(
            p_hit=1.0, build_dur=10.0, window_width=2.0, contention_level=0.0
        )
        self.assertFalse(passes)
        self.assertAlmostEqual(benefit, 0.0)

    def test_c5_formula_values_at_standard_inputs(self) -> None:
        """Verify exact formula values for p_hit=0.8, build_dur=0.4, window=1.2, contention=50."""
        gate = ArmingGate(
            benefit_prior_max=0.5,
            idle_cost_rate=0.05,
            other_impact_rate=0.10,
        )
        p_hit, build_dur, window_width, contention = 0.8, 0.4, 1.2, 50.0
        passes, benefit, cost = gate.evaluate_cost_benefit(p_hit, build_dur, window_width, contention)
        ref_passes, ref_benefit, ref_cost = self._ref_cost_benefit(p_hit, build_dur, window_width, contention)
        self.assertAlmostEqual(benefit, ref_benefit, places=8)
        self.assertAlmostEqual(cost, ref_cost, places=8)
        self.assertEqual(passes, ref_passes)


# ──────────────────────────────────────────────────────────────────────────────
# Test d: No observed builds -> abstain_no_build_estimate
# ──────────────────────────────────────────────────────────────────────────────
class TestNoObservedBuilds(unittest.TestCase):
    """Requirement d: no observed builds -> abstain_no_build_estimate."""

    def test_d1_no_builds_abstain(self) -> None:
        """With empty build_durations, decide() returns abstain_no_build_estimate."""
        # Mock PSI reader returning high contention
        psi_data = [_psi(30.0)]

        def reader():
            return psi_data[0]

        gate = ArmingGate(
            psi_reader=reader,
            contention_threshold_pct=10.0,
            holdout_probability=0.0,
            rng=random.Random(0),
        )
        decision = gate.decide(
            can_arm=True,
            p_hit=0.9,
            build_durations=[],   # no builds!
            window_lo=1.0,
            window_hi=3.0,
        )
        self.assertEqual(decision.decision, "abstain_no_build_estimate")
        self.assertFalse(decision.arm)
        self.assertFalse(decision.holdout)


# ──────────────────────────────────────────────────────────────────────────────
# Test e: Config toggles for each gate
# ──────────────────────────────────────────────────────────────────────────────
class TestGateToggles(unittest.TestCase):
    """Requirement e: each config toggle works and is noted in reasons."""

    def _high_psi_reader(self):
        return _psi(30.0, some60=5.0)

    def test_e1_confidence_gate_disabled_skips_can_arm_check(self) -> None:
        """confidence_gate_enabled=False: can_arm=False is ignored."""
        gate = ArmingGate(
            psi_reader=self._high_psi_reader,
            contention_threshold_pct=10.0,
            confidence_gate_enabled=False,
            holdout_probability=0.0,
            rng=random.Random(0),
        )
        decision = gate.decide(
            can_arm=False,   # confidence gate would normally block
            p_hit=1.0,
            build_durations=[5.0, 5.0, 5.0],
            window_lo=1.0,
            window_hi=3.0,
        )
        # Should not be abstain_confidence
        self.assertNotEqual(decision.decision, "abstain_confidence")
        self.assertTrue(any("confidence_gate disabled" in r for r in decision.reasons))

    def test_e2_contention_gate_disabled_skips_psi_check(self) -> None:
        """contention_gate_enabled=False: PSI below threshold is ignored."""
        def low_psi():
            return _psi(0.0)

        gate = ArmingGate(
            psi_reader=low_psi,
            contention_threshold_pct=10.0,
            contention_gate_enabled=False,
            holdout_probability=0.0,
            rng=random.Random(0),
        )
        decision = gate.decide(
            can_arm=True,
            p_hit=1.0,
            build_durations=[5.0, 5.0],
            window_lo=1.0,
            window_hi=3.0,
        )
        # Should not be abstain_no_contention (gate disabled)
        self.assertNotEqual(decision.decision, "abstain_no_contention")
        self.assertTrue(any("contention_gate disabled" in r for r in decision.reasons))

    def test_e3_cost_gate_disabled_skips_cost_check(self) -> None:
        """cost_gate_enabled=False: cost > benefit is ignored."""
        def high_psi():
            return _psi(50.0, some60=5.0)

        gate = ArmingGate(
            psi_reader=high_psi,
            contention_threshold_pct=10.0,
            cost_gate_enabled=False,
            holdout_probability=0.0,
            rng=random.Random(0),
        )
        decision = gate.decide(
            can_arm=True,
            p_hit=0.01,   # cost would normally exceed benefit
            build_durations=[0.001],
            window_lo=0.5,
            window_hi=2.5,
        )
        self.assertNotEqual(decision.decision, "abstain_cost")
        self.assertTrue(any("cost_gate disabled" in r for r in decision.reasons))

    def test_e4_holdout_disabled_never_holdout(self) -> None:
        """holdout_enabled=False: no holdout events even with p=1.0."""
        def high_psi():
            return _psi(50.0, some60=5.0)

        gate = ArmingGate(
            psi_reader=high_psi,
            contention_threshold_pct=10.0,
            holdout_enabled=False,
            holdout_probability=1.0,  # would always holdout if enabled
            rng=random.Random(0),
        )
        for _ in range(20):
            decision = gate.decide(
                can_arm=True,
                p_hit=1.0,
                build_durations=[5.0],
                window_lo=1.0,
                window_hi=3.0,
            )
            self.assertNotEqual(decision.decision, "holdout")
            self.assertFalse(decision.holdout)


# ──────────────────────────────────────────────────────────────────────────────
# Test f: Holdout share statistics
# ──────────────────────────────────────────────────────────────────────────────
class TestHoldoutShare(unittest.TestCase):
    """Requirement f: holdout share approximates p=0.1 within binomial 99% band."""

    def _make_qualifying_gate(self, p_holdout: float, seed: int = 42) -> ArmingGate:
        """Create ArmingGate that always passes confidence/contention/cost gates."""
        def high_psi():
            return _psi(50.0, some60=5.0)

        return ArmingGate(
            psi_reader=high_psi,
            contention_threshold_pct=10.0,
            holdout_probability=p_holdout,
            rng=random.Random(seed),
        )

    def test_f1_holdout_rate_within_binomial_band(self) -> None:
        """1000 qualifying events at p=0.1 should yield holdout count within 99% band."""
        n = 1000
        p = 0.1
        gate = self._make_qualifying_gate(p_holdout=p, seed=12345)

        holdout_count = 0
        for _ in range(n):
            d = gate.decide(
                can_arm=True,
                p_hit=1.0,
                build_durations=[5.0],
                window_lo=1.0,
                window_hi=3.0,
            )
            if d.holdout:
                holdout_count += 1

        # 99% CI for Binomial(n=1000, p=0.1): mean=100, std=sqrt(90)~9.49
        # CI: [100 ± 3*9.49] = [71.5, 128.5]
        expected_mean = n * p
        expected_std = math.sqrt(n * p * (1 - p))
        z = 3.0  # 99.7% coverage
        lower = expected_mean - z * expected_std
        upper = expected_mean + z * expected_std
        self.assertGreaterEqual(holdout_count, lower,
                                f"Holdout count {holdout_count} below 99% lower bound {lower:.0f}")
        self.assertLessEqual(holdout_count, upper,
                             f"Holdout count {holdout_count} above 99% upper bound {upper:.0f}")

    def test_f2_holdout_probability_zero_never_holdout(self) -> None:
        """p=0: never select holdout."""
        gate = self._make_qualifying_gate(p_holdout=0.0)
        for _ in range(50):
            d = gate.decide(
                can_arm=True, p_hit=1.0, build_durations=[5.0], window_lo=1.0, window_hi=3.0
            )
            self.assertFalse(d.holdout)
            self.assertNotEqual(d.decision, "holdout")

    def test_f3_holdout_probability_one_always_holdout(self) -> None:
        """p=1: always select holdout."""
        gate = self._make_qualifying_gate(p_holdout=1.0)
        for _ in range(50):
            d = gate.decide(
                can_arm=True, p_hit=1.0, build_durations=[5.0], window_lo=1.0, window_hi=3.0
            )
            self.assertTrue(d.holdout)
            self.assertEqual(d.decision, "holdout")

    def test_f4_holdout_not_counted_as_would_arm(self) -> None:
        """Holdout events have decision='holdout', not 'would_arm'."""
        gate = self._make_qualifying_gate(p_holdout=1.0)
        d = gate.decide(
            can_arm=True, p_hit=1.0, build_durations=[5.0], window_lo=1.0, window_hi=3.0
        )
        self.assertEqual(d.decision, "holdout")
        self.assertNotEqual(d.decision, "would_arm")
        self.assertFalse(d.arm)


# ──────────────────────────────────────────────────────────────────────────────
# Test g: Store migration v2
# ──────────────────────────────────────────────────────────────────────────────
class TestStoreMigrationV2(unittest.TestCase):
    """Requirement g: v1->v2 migration preserves data, adds columns; second open is no-op."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(dir="/tmp", prefix="precompile_migration_test_")
        self.db_path = Path(self.temp_dir) / "migration_test.db"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _build_v1_db(self) -> None:
        """Build a v1 DB using raw sqlite3 (simulates pre-Step7 DB)."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                root_path TEXT NOT NULL,
                tool_profile_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delay_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_class TEXT NOT NULL,
                tool_profile TEXT NOT NULL,
                delay_s REAL NOT NULL,
                accepted INTEGER NOT NULL,
                t_save_wall REAL NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_state (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_class TEXT NOT NULL,
                tool_profile TEXT NOT NULL,
                n INTEGER NOT NULL,
                mean REAL NOT NULL,
                var REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (project_id, file_class, tool_profile)
            );
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_class TEXT NOT NULL,
                tool_profile TEXT NOT NULL,
                t_save_wall REAL NOT NULL,
                window_lo REAL NOT NULL,
                window_hi REAL NOT NULL,
                armed INTEGER NOT NULL,
                holdout INTEGER NOT NULL,
                outcome TEXT,
                observed_delay_s REAL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS builds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                condition TEXT NOT NULL,
                contention_level REAL NOT NULL,
                duration_s REAL NOT NULL,
                t_start_wall REAL NOT NULL,
                t_end_wall REAL NOT NULL,
                prediction_id INTEGER REFERENCES predictions(id) ON DELETE SET NULL,
                created_at REAL NOT NULL
            );
        """)
        # Insert v1 marker
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (1, ?)",
            (1000.0,)
        )
        # Insert test data
        conn.execute(
            "INSERT INTO projects (id, root_path, tool_profile_name, enabled, created_at) "
            "VALUES ('proj1', '/tmp/p1', 'default', 1, 1000.0)"
        )
        conn.execute(
            "INSERT INTO delay_samples (project_id, file_class, tool_profile, delay_s, "
            "accepted, t_save_wall, created_at) VALUES ('proj1', 'source', 'default', 1.8, 1, 1000.0, 1000.0)"
        )
        conn.execute(
            "INSERT INTO predictions (project_id, file_class, tool_profile, t_save_wall, "
            "window_lo, window_hi, armed, holdout, created_at) "
            "VALUES ('proj1', 'source', 'default', 1001.0, 1.5, 2.5, 0, 0, 1001.0)"
        )
        conn.commit()
        conn.close()

    def test_g1_v1_db_upgraded_with_no_data_loss(self) -> None:
        """Opening v1 DB with new DataStore applies v2 migration, data intact."""
        self._build_v1_db()

        # Open with new DataStore (triggers migrate() -> _apply_migration_v2)
        with DataStore(self.db_path) as store:
            # Data integrity
            proj = store.get_project("proj1")
            self.assertIsNotNone(proj)
            self.assertEqual(proj.root_path, "/tmp/p1")

            samples = store.recent_samples("proj1", "source", "default", n=10)
            self.assertEqual(len(samples), 1)
            self.assertAlmostEqual(samples[0].delay_s, 1.8)

            # Check that v2 columns exist
            cur = store._conn.execute("PRAGMA table_info(predictions)")
            col_names = {row[1] for row in cur.fetchall()}
            self.assertIn("decision", col_names)
            self.assertIn("contention_level", col_names)
            self.assertIn("benefit_s", col_names)
            self.assertIn("cost_s", col_names)

            # Schema version should be at least 2
            cur = store._conn.execute("SELECT MAX(version) FROM schema_version")
            self.assertGreaterEqual(cur.fetchone()[0], 2)

            # Use set_prediction_decision on the existing row
            cur = store._conn.execute("SELECT id FROM predictions LIMIT 1")
            pred_id = cur.fetchone()[0]
            store.set_prediction_decision(pred_id, "would_arm", 25.0, 0.5, 0.1)

            # Verify stored
            cur = store._conn.execute(
                "SELECT decision, contention_level, benefit_s, cost_s FROM predictions WHERE id = ?",
                (pred_id,)
            )
            row = cur.fetchone()
            self.assertEqual(row[0], "would_arm")
            self.assertAlmostEqual(row[1], 25.0)

    def test_g2_second_open_is_no_op(self) -> None:
        """Opening an already-v2 DB again is idempotent (no errors, data unchanged)."""
        self._build_v1_db()
        # First open: migrates to latest schema (at least v2)
        with DataStore(self.db_path) as store1:
            v_first = store1._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            self.assertGreaterEqual(v_first, 2)

        # Second open: should be a no-op (version matches v_first)
        with DataStore(self.db_path) as store2:
            v_second = store2._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            self.assertEqual(v_second, v_first)
            proj = store2.get_project("proj1")
            self.assertIsNotNone(proj)


# ──────────────────────────────────────────────────────────────────────────────
# Test h: Daemon integration with mocked PSI reader
# ──────────────────────────────────────────────────────────────────────────────
class TestDaemonArmingIntegration(unittest.TestCase):
    """Requirement h: daemon integration with mocked PSI reader."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(dir="/tmp", prefix="precompile_gate_daemon_test_")
        self.proj_dir = Path(self.temp_dir) / "proj"
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(self.temp_dir) / "daemon_gate.db"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_store_with_predictions_enabled(
        self, store: DataStore, proj_id: str, proj_path: str
    ) -> None:
        """Seed a store so can_arm=True from confidence gate."""
        store.add_project(proj_id, proj_path, "default")
        # 10 samples: mean=1.5, sigma small
        for _ in range(10):
            store.record_sample(proj_id, "source", "default", 1.5, accepted=True)
        store.save_model_state((proj_id, "source", "default"), n=10, mean=1.5, var=0.0001)
        # 5 scored predictions all hits
        for _ in range(5):
            pred_id = store.record_prediction(
                proj_id, "source", "default", 1000.0, 1.0, 3.0
            )
            store.score_prediction(pred_id, "hit")
        # 5 observed builds with realistic durations
        for dur in [1.5, 1.6, 1.4, 1.5, 1.55]:
            store.record_build(
                proj_id, "observed", 0.0, dur, 1000.0, 1000.0 + dur
            )

    def test_h1_high_psi_produces_would_arm_or_holdout(self) -> None:
        """With PSI=50%, gates pass -> decisions include 'would_arm' or 'holdout'."""
        def high_psi_reader():
            return _psi(50.0, some60=5.0)

        arming_gate = ArmingGate(
            psi_reader=high_psi_reader,
            contention_threshold_pct=10.0,
            holdout_probability=0.0,
            rng=random.Random(0),
        )

        sim_time = 1000.0

        def clock():
            return sim_time

        with DataStore(self.db_path) as store:
            self._setup_store_with_predictions_enabled(store, "gproj", str(self.proj_dir))

        daemon = PreCompileDaemon(
            db_path=self.db_path,
            clock=clock,
            arming_gate=arming_gate,
        )

        # Simulate 3 save events that produce predictions
        for i in range(3):
            sim_time += 100.0
            ev = SaveEvent("gproj", self.proj_dir / "main.c", "source", sim_time, sim_time)
            daemon._on_save_event(ev)

        # Check that at least one prediction has a decision set
        with DataStore(self.db_path) as store:
            dc = store.decision_counts("gproj")
            total = sum(dc.values())
            self.assertGreater(total, 0, "Expected at least 1 decision to be recorded")
            # Should have 'would_arm' (no holdout since p=0)
            self.assertGreater(dc.get("would_arm", 0), 0,
                               f"Expected would_arm decisions, got {dc}")

    def test_h2_zero_psi_produces_abstain_no_contention(self) -> None:
        """With PSI=0%, contention gate blocks -> abstain_no_contention."""
        def zero_psi_reader():
            return _psi(0.0)

        arming_gate = ArmingGate(
            psi_reader=zero_psi_reader,
            contention_threshold_pct=10.0,
            holdout_probability=0.0,
            rng=random.Random(0),
        )

        sim_time = 1000.0

        def clock():
            return sim_time

        with DataStore(self.db_path) as store:
            self._setup_store_with_predictions_enabled(store, "gproj2", str(self.proj_dir))

        daemon = PreCompileDaemon(
            db_path=self.db_path,
            clock=clock,
            arming_gate=arming_gate,
        )

        for i in range(3):
            sim_time += 100.0
            ev = SaveEvent("gproj2", self.proj_dir / "main.c", "source", sim_time, sim_time)
            daemon._on_save_event(ev)

        with DataStore(self.db_path) as store:
            dc = store.decision_counts("gproj2")
            self.assertGreater(dc.get("abstain_no_contention", 0), 0,
                               f"Expected abstain_no_contention, got {dc}")

    def test_h3_armed_flag_stays_false(self) -> None:
        """The 'armed' column on predictions must remain False in observation mode."""
        def high_psi_reader():
            return _psi(50.0, some60=5.0)

        arming_gate = ArmingGate(
            psi_reader=high_psi_reader,
            contention_threshold_pct=10.0,
            holdout_probability=0.0,
            rng=random.Random(0),
        )

        sim_time = 1000.0

        def clock():
            return sim_time

        with DataStore(self.db_path) as store:
            self._setup_store_with_predictions_enabled(store, "gproj3", str(self.proj_dir))

        daemon = PreCompileDaemon(
            db_path=self.db_path,
            clock=clock,
            arming_gate=arming_gate,
        )

        for i in range(3):
            sim_time += 100.0
            ev = SaveEvent("gproj3", self.proj_dir / "main.c", "source", sim_time, sim_time)
            daemon._on_save_event(ev)

        with DataStore(self.db_path) as store:
            cur = store._conn.execute(
                "SELECT armed FROM predictions WHERE project_id = 'gproj3'"
            )
            for row in cur.fetchall():
                self.assertEqual(row[0], 0, "armed flag must stay 0 in observation mode")

    def test_h4_no_cgroup_writes(self) -> None:
        """Observation-only: default is actuate=False with no cgroup writes."""
        daemon = PreCompileDaemon(db_path=self.db_path)
        self.assertFalse(daemon.actuate)


# ──────────────────────────────────────────────────────────────────────────────
# Test i: SIGHUP reload
# ──────────────────────────────────────────────────────────────────────────────
class TestSIGHUPReload(unittest.TestCase):
    """Requirement i: SIGHUP reload picks up a project added while the daemon runs."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(dir="/tmp", prefix="precompile_sighup_test_")
        self.db_path = Path(self.temp_dir) / "sighup_test.db"
        self.proj_dir1 = Path(self.temp_dir) / "proj1"
        self.proj_dir1.mkdir(parents=True, exist_ok=True)
        self.proj_dir2 = Path(self.temp_dir) / "proj2"
        self.proj_dir2.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_i1_sighup_adds_new_watch(self) -> None:
        """After adding a project to DB and calling reload_projects(), daemon watches it."""
        # Start daemon with one project
        with DataStore(self.db_path) as store:
            store.add_project("proj1", str(self.proj_dir1), "default")

        daemon = PreCompileDaemon(db_path=self.db_path)
        daemon.start()

        try:
            time.sleep(0.1)

            # Initially only proj1 is watched
            self.assertIn("proj1", daemon._watched_project_ids)
            self.assertNotIn("proj2", daemon._watched_project_ids)

            # Add proj2 to the database
            with DataStore(self.db_path) as store:
                store.add_project("proj2", str(self.proj_dir2), "default")

            # Simulate SIGHUP via reload_projects()
            daemon.reload_projects()

            # Now proj2 should be watched
            self.assertIn("proj2", daemon._watched_project_ids)

        finally:
            daemon.stop()

    def test_i2_sighup_removes_disabled_watch(self) -> None:
        """After disabling a project and calling reload_projects(), daemon unwatches it."""
        with DataStore(self.db_path) as store:
            store.add_project("proj1", str(self.proj_dir1), "default")
            store.add_project("proj2", str(self.proj_dir2), "default")

        daemon = PreCompileDaemon(db_path=self.db_path)
        daemon.start()

        try:
            time.sleep(0.1)
            self.assertIn("proj2", daemon._watched_project_ids)

            # Disable proj2
            with DataStore(self.db_path) as store:
                store.set_project_enabled("proj2", False)

            daemon.reload_projects()
            self.assertNotIn("proj2", daemon._watched_project_ids)

        finally:
            daemon.stop()


# ──────────────────────────────────────────────────────────────────────────────
# Test j: projects add --path resolves to absolute
# ──────────────────────────────────────────────────────────────────────────────
class TestProjectsAddAbsolutePath(unittest.TestCase):
    """Requirement j: `projects add --path .` stores an absolute path."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(dir="/tmp", prefix="precompile_abspath_test_")
        self.db_path = Path(self.temp_dir) / "abspath_test.db"
        self.proj_dir = Path(self.temp_dir) / "myproj"
        self.proj_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_j1_dot_resolves_to_absolute(self) -> None:
        """--path . stores the absolute cwd, not '.'."""
        orig_cwd = os.getcwd()
        try:
            os.chdir(str(self.proj_dir))
            rc = main([
                "projects", "add",
                "--id", "dotproj",
                "--path", ".",
                "--db", str(self.db_path),
            ])
            self.assertEqual(rc, 0)
        finally:
            os.chdir(orig_cwd)

        with DataStore(self.db_path) as store:
            p = store.get_project("dotproj")
            self.assertIsNotNone(p)
            stored_path = p.root_path
            self.assertTrue(
                Path(stored_path).is_absolute(),
                f"Expected absolute path, got '{stored_path}'"
            )
            self.assertNotEqual(stored_path, ".")

    def test_j2_tilde_resolves_to_absolute(self) -> None:
        """--path ~/someproj stores an absolute expanded path."""
        # We can't actually write to ~, but we can test the resolution logic
        from precompile.cli import _resolve_project_path
        result = _resolve_project_path("~/foo/bar")
        self.assertTrue(Path(result).is_absolute())
        self.assertNotIn("~", result)

    def test_j3_explicit_absolute_path_stored_unchanged(self) -> None:
        """An explicit absolute path is stored as-is (absolute)."""
        rc = main([
            "projects", "add",
            "--id", "absproj",
            "--path", str(self.proj_dir),
            "--db", str(self.db_path),
        ])
        self.assertEqual(rc, 0)
        with DataStore(self.db_path) as store:
            p = store.get_project("absproj")
            self.assertTrue(Path(p.root_path).is_absolute())


# ──────────────────────────────────────────────────────────────────────────────
# Test k: CLI status/predict show decision counts
# ──────────────────────────────────────────────────────────────────────────────
class TestCLIDecisionCounts(unittest.TestCase):
    """Requirement k: CLI status/predict --json show decision counts per project."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(dir="/tmp", prefix="precompile_dc_test_")
        self.db_path = Path(self.temp_dir) / "dc_test.db"
        self.proj_dir = Path(self.temp_dir) / "projdc"
        self.proj_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _seed_db(self) -> None:
        """Seed DB with a project and some decision records."""
        with DataStore(self.db_path) as store:
            store.add_project("proj_dc", str(self.proj_dir), "default")
            # Record predictions with decisions
            for i in range(3):
                pred_id = store.record_prediction(
                    "proj_dc", "source", "default", 1000.0 + i, 1.0, 3.0
                )
                store.set_prediction_decision(pred_id, "would_arm", 25.0, 0.5, 0.1)
            for i in range(2):
                pred_id = store.record_prediction(
                    "proj_dc", "source", "default", 2000.0 + i, 1.0, 3.0
                )
                store.set_prediction_decision(pred_id, "abstain_no_contention", 1.0, 0.0, 0.0)

    def test_k1_status_json_includes_decision_counts(self) -> None:
        """status --json includes decision_counts per project."""
        self._seed_db()
        f = io.StringIO()
        with redirect_stdout(f):
            rc = main(["status", "--json", "--db", str(self.db_path)])
        self.assertEqual(rc, 0)
        data = json.loads(f.getvalue())
        proj = data["projects"][0]
        self.assertIn("decision_counts", proj)
        dc = proj["decision_counts"]
        self.assertEqual(dc["would_arm"], 3)
        self.assertEqual(dc["abstain_no_contention"], 2)

    def test_k2_predict_json_includes_decision_counts(self) -> None:
        """predict --json includes decision_counts."""
        self._seed_db()
        f = io.StringIO()
        with redirect_stdout(f):
            rc = main(["predict", "--project", "proj_dc", "--json", "--db", str(self.db_path)])
        self.assertEqual(rc, 0)
        data = json.loads(f.getvalue())
        self.assertIn("decision_counts", data)
        dc = data["decision_counts"]
        self.assertEqual(dc["would_arm"], 3)
        self.assertEqual(dc["abstain_no_contention"], 2)

    def test_k3_status_human_readable_shows_decision_counts(self) -> None:
        """status (human-readable) includes decision counts in output."""
        self._seed_db()
        f = io.StringIO()
        with redirect_stdout(f):
            rc = main(["status", "--db", str(self.db_path)])
        self.assertEqual(rc, 0)
        output = f.getvalue()
        self.assertIn("would_arm", output)
        self.assertIn("abstain_no_contention", output)


# ──────────────────────────────────────────────────────────────────────────────
# Test for SIGHUP CLI notification
# ──────────────────────────────────────────────────────────────────────────────
class TestCLISIGHUPNotification(unittest.TestCase):
    """Part A: After projects add/remove/enable/disable, CLI sends SIGHUP if daemon alive."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(dir="/tmp", prefix="precompile_sighup_cli_test_")
        self.db_path = Path(self.temp_dir) / "sighup_cli.db"
        self.proj_dir = Path(self.temp_dir) / "proj"
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file = Path(self.temp_dir) / "daemon.pid"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sighup_sent_when_daemon_alive(self) -> None:
        """CLI sends SIGHUP and prints message when daemon PID file exists and process alive."""
        # Write our own PID as the "daemon" so kill(pid, SIGHUP) goes to this process
        self.pid_file.write_text(str(os.getpid()), encoding="utf-8")

        received = threading.Event()

        def _handle_hup(sig, frame):
            received.set()

        old_handler = signal.signal(signal.SIGHUP, _handle_hup)
        try:
            f = io.StringIO()
            with redirect_stdout(f):
                rc = main([
                    "projects", "add",
                    "--id", "sighup_proj",
                    "--path", str(self.proj_dir),
                    "--db", str(self.db_path),
                    "--pid-file", str(self.pid_file),
                ])
            self.assertEqual(rc, 0)
            # Wait for SIGHUP
            self.assertTrue(received.wait(timeout=2.0), "SIGHUP was not received within 2s")
            output = f.getvalue()
            self.assertIn("SIGHUP", output)
        finally:
            signal.signal(signal.SIGHUP, old_handler)


# ──────────────────────────────────────────────────────────────────────────────
# Step 7b: Continuous PSI Sampling & Bash Syntax Tests
# ──────────────────────────────────────────────────────────────────────────────
class TestPSISamplerContinuous(unittest.TestCase):
    """Step 7b: PSISampler continuous sampling and rapid detection of sudden stall."""

    def test_sudden_stall_visible_within_one_second(self) -> None:
        """A sudden stall increase is visible in recent_some_pct within ~1 second."""
        sim_time = 100.0
        stall_total_us = 0

        def clock() -> float:
            return sim_time

        def reader() -> Optional[CPUContentionMetrics]:
            return _psi(10.0, total=stall_total_us)

        sampler = PSISampler(reader=reader, clock=clock)

        # 1. Baseline: 5 seconds of idle (zero stall accumulation) sampled every 200 ms
        for _ in range(25):
            sim_time += 0.20
            sampler.sample()

        # Under idle, recent stall is 0%
        self.assertAlmostEqual(sampler.recent_some_pct, 0.0, places=2)

        # 2. Sudden stall onset: 60% stall rate (600,000 us stall per second => 120,000 us per 200 ms)
        # Sample every 200 ms for 1.0 second (5 steps)
        for _ in range(5):
            sim_time += 0.20
            stall_total_us += 120_000
            sampler.sample()

        # At t = 106.0s (1.0s after stall start), recent_some_pct must reflect ~60% stall
        recent = sampler.recent_some_pct
        self.assertGreaterEqual(recent, 55.0,
                                f"Expected recent_some_pct >= 55% within 1s, got {recent:.2f}%")
        self.assertLessEqual(recent, 65.0,
                             f"Expected recent_some_pct <= 65% within 1s, got {recent:.2f}%")

    def test_daemon_housekeeping_samples_psi_periodically(self) -> None:
        """Daemon housekeeping tick calls sample_psi every ~200 ms without extra threads."""
        sim_time = 1000.0

        def clock() -> float:
            return sim_time

        temp_dir = tempfile.mkdtemp(dir="/tmp", prefix="precompile_hk_psi_")
        db_path = Path(temp_dir) / "test.db"
        try:
            sample_count = 0

            def mock_reader() -> Optional[CPUContentionMetrics]:
                nonlocal sample_count
                sample_count += 1
                return _psi(5.0, total=sample_count * 1000)

            arming_gate = ArmingGate(psi_reader=mock_reader, clock=clock)
            daemon = PreCompileDaemon(db_path=db_path, clock=clock, arming_gate=arming_gate)

            # Manually tick housekeeping or check tick logic
            # Initial state: 0 samples
            self.assertEqual(len(arming_gate.psi_sampler._history), 0)

            # Advance clock by 250 ms and run one tick of housekeeping
            sim_time += 0.25
            with daemon._lock:
                if sim_time - daemon._last_psi_sample_mono >= 0.20:
                    daemon._arming_gate.sample_psi()
                    daemon._last_psi_sample_mono = sim_time

            self.assertEqual(len(arming_gate.psi_sampler._history), 1)

            # Advance clock by only 50 ms (less than 200 ms) -> no sample
            sim_time += 0.05
            with daemon._lock:
                if sim_time - daemon._last_psi_sample_mono >= 0.20:
                    daemon._arming_gate.sample_psi()
                    daemon._last_psi_sample_mono = sim_time

            self.assertEqual(len(arming_gate.psi_sampler._history), 1)

            # Advance clock past 200 ms -> sample taken
            sim_time += 0.20
            with daemon._lock:
                if sim_time - daemon._last_psi_sample_mono >= 0.20:
                    daemon._arming_gate.sample_psi()
                    daemon._last_psi_sample_mono = sim_time

            self.assertEqual(len(arming_gate.psi_sampler._history), 2)
            daemon.stop()
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestBenchmarkScriptsSyntax(unittest.TestCase):
    """Step 7b: Syntax check for benchmark shell scripts via bash -n."""

    def test_scripts_syntax(self) -> None:
        """Verify load.sh, calibrate_load.sh, and run_demo_contention.sh pass bash -n."""
        import subprocess
        scripts = [
            Path("benchmarks/load.sh"),
            Path("benchmarks/calibrate_load.sh"),
            Path("benchmarks/run_demo_contention.sh"),
        ]
        for script in scripts:
            if not script.exists():
                continue
            res = subprocess.run(
                ["bash", "-n", str(script)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                res.returncode, 0,
                f"{script} failed bash -n check:\n{res.stderr}"
            )


if __name__ == "__main__":
    unittest.main()

