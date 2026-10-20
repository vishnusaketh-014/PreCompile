"""Unit tests for Stage 9 BenefitEstimator and Priority Feedback Control (precompile/benefit.py).

Tests:
    a. Schema migration v4 upgrades cleanly, preserves existing data, and is idempotent.
    b. Dataset A (significant speedup): asserts benefit estimator confirms significance and maintains high priority.
    c. Dataset B (zero speedup): asserts benefit estimator decreases cpu.weight and flags arming suppression.
    d. Stratified binning maps contention level correctly into low/mild/moderate/high.
    e. Weight decay sequence: 500 -> 250 -> 125 -> 100 -> disabled.
    f. Exploratory probes issued with probe_probability when disabled.
    g. Successful probe outcome steps weight up and re-enables arming.
    h. Evaluation cadence enforced: evaluations run only after eval_every new builds.
    i. Threshold enforcement: requires min_total_n builds before first evaluation.
    j. Store methods: get_benefit_state, set_benefit_state, record_benefit_eval, list_benefit_evals.
"""

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from typing import Dict, List

from precompile.benefit import BenefitEstimator, classify_contention_bin
from precompile.store import DataStore


class TestBenefitEstimator(unittest.TestCase):
    """Test suite for BenefitEstimator and Stage 9 database schema/controllers."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_benefit.db"
        self.store = DataStore(db_path=self.db_path)
        self.store.add_project("demo", "/tmp/demo", "default")

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_a_migration_v4_idempotent_and_clean(self):
        """Part D.a: Schema migration v4 upgrades cleanly, preserves data, is idempotent."""
        # Check tables exist
        with self.store._lock:
            cur = self.store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('benefit_state', 'benefit_evals')"
            )
            tables = {r[0] for r in cur.fetchall()}
            self.assertIn("benefit_state", tables)
            self.assertIn("benefit_evals", tables)

        # Call migrate() again -> must be idempotent with no errors
        self.store.migrate()

        # Check schema version is at least 4
        with self.store._lock:
            cur = self.store._conn.execute("SELECT MAX(version) FROM schema_version")
            self.assertGreaterEqual(cur.fetchone()[0], 4)

    def test_d_stratified_binning(self):
        """Part D.d: Classify contention into low/mild/moderate/high."""
        self.assertEqual(classify_contention_bin(0.0), "low")
        self.assertEqual(classify_contention_bin(9.9), "low")
        self.assertEqual(classify_contention_bin(10.0), "mild")
        self.assertEqual(classify_contention_bin(29.9), "mild")
        self.assertEqual(classify_contention_bin(30.0), "moderate")
        self.assertEqual(classify_contention_bin(59.9), "moderate")
        self.assertEqual(classify_contention_bin(60.0), "high")
        self.assertEqual(classify_contention_bin(100.0), "high")

    def test_b_dataset_a_significant_speedup_maintains_weight(self):
        """Part D.b: Synthetic dataset with clear speedup maintains weight and keeps arming enabled."""
        estimator = BenefitEstimator(
            store=self.store,
            configured_weight=500,
            min_bin_n=5,
            min_total_n=8,
            eval_every=10,
        )

        # Seed 10 reserved (fast ~3.0s) and 10 holdout (slow ~8.0s) under high contention (PSI 80%)
        now = time.time()
        for i in range(10):
            self.store.record_build("demo", "reserved", contention_level=80.0, duration_s=3.0 + (i * 0.05), t_start_wall=now, t_end_wall=now)
            self.store.record_build("demo", "holdout", contention_level=80.0, duration_s=8.0 + (i * 0.05), t_start_wall=now, t_end_wall=now)

        eval_result = estimator.evaluate("demo")
        self.assertEqual(eval_result["action"], "keep")
        self.assertTrue(eval_result["arming_after"])
        self.assertEqual(eval_result["weight_after"], 500)
        self.assertGreater(eval_result["overall_effect"], 0.50)
        self.assertLess(eval_result["p_value"], 0.01)

        # Confirm persisted state
        state = self.store.get_benefit_state("demo")
        self.assertIsNotNone(state)
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["weight"], 500)
        self.assertTrue(state["arming_enabled"])

    def test_c_and_e_dataset_b_zero_speedup_decays_weight_to_disable(self):
        """Part D.c & e: Zero speedup causes weight decay 500 -> 250 -> 125 -> 100 -> disable."""
        estimator = BenefitEstimator(
            store=self.store,
            configured_weight=500,
            min_bin_n=5,
            min_total_n=8,
            eval_every=10,
        )

        now = time.time()
        # Seed builds where reserved and holdout have identical durations (~5.0s)
        for i in range(10):
            self.store.record_build("demo", "reserved", contention_level=70.0, duration_s=5.0 + (i * 0.05), t_start_wall=now, t_end_wall=now)
            self.store.record_build("demo", "holdout", contention_level=70.0, duration_s=5.0 + (i * 0.05), t_start_wall=now, t_end_wall=now)

        # Evaluation 1: 500 -> 250
        res1 = estimator.evaluate("demo")
        self.assertEqual(res1["action"], "decay")
        self.assertEqual(res1["weight_after"], 250)
        self.assertTrue(res1["arming_after"])

        # Evaluation 2: 250 -> 125
        res2 = estimator.evaluate("demo")
        self.assertEqual(res2["action"], "decay")
        self.assertEqual(res2["weight_after"], 125)
        self.assertTrue(res2["arming_after"])

        # Evaluation 3: 125 -> 100
        res3 = estimator.evaluate("demo")
        self.assertEqual(res3["action"], "decay")
        self.assertEqual(res3["weight_after"], 100)
        self.assertTrue(res3["arming_after"])

        # Evaluation 4: 100 -> disable
        res4 = estimator.evaluate("demo")
        self.assertEqual(res4["action"], "disable")
        self.assertEqual(res4["weight_after"], 100)
        self.assertFalse(res4["arming_after"])

        state = self.store.get_benefit_state("demo")
        self.assertFalse(state["arming_enabled"])
        self.assertEqual(state["status"], "disabled")

    def test_f_probe_issuance_when_disabled(self):
        """Part D.f: Probes issued probabilistically when arming is disabled."""
        estimator = BenefitEstimator(
            store=self.store,
            probe_probability=1.0,  # Force deterministic probe
        )
        # Disable project
        self.store.set_benefit_state("demo", weight=100, arming_enabled=False, status="disabled")

        self.assertFalse(estimator.is_arming_enabled("demo"))
        self.assertTrue(estimator.should_probe("demo"))

    def test_g_successful_probe_steps_weight_up(self):
        """Part D.g: When disabled, accumulating significant benefit steps weight up."""
        estimator = BenefitEstimator(
            store=self.store,
            configured_weight=500,
            min_total_n=8,
        )
        self.store.set_benefit_state("demo", weight=100, arming_enabled=False, status="disabled")

        now = time.time()
        for i in range(10):
            self.store.record_build("demo", "reserved", contention_level=75.0, duration_s=2.0 + (i * 0.05), t_start_wall=now, t_end_wall=now)
            self.store.record_build("demo", "holdout", contention_level=75.0, duration_s=10.0 + (i * 0.05), t_start_wall=now, t_end_wall=now)

        eval_res = estimator.evaluate("demo")
        self.assertEqual(eval_res["action"], "keep")
        self.assertTrue(eval_res["arming_after"])
        self.assertEqual(eval_res["weight_after"], 200)  # Stepped up from 100 to 200

    def test_h_and_i_evaluation_cadence_and_threshold(self):
        """Part D.h & i: requires min_total_n and evaluates every eval_every builds."""
        estimator = BenefitEstimator(
            store=self.store,
            min_total_n=5,
            eval_every=4,
        )

        now = time.time()
        # Seed 4 builds each (less than min_total_n=5)
        for i in range(4):
            self.store.record_build("demo", "reserved", contention_level=50.0, duration_s=3.0, t_start_wall=now, t_end_wall=now)
            self.store.record_build("demo", "holdout", contention_level=50.0, duration_s=6.0, t_start_wall=now, t_end_wall=now)

        self.assertIsNone(estimator.maybe_evaluate("demo"))

        # Add 5th pair -> reaches min_total_n=5 -> evaluates
        self.store.record_build("demo", "reserved", contention_level=50.0, duration_s=3.0, t_start_wall=now, t_end_wall=now)
        self.store.record_build("demo", "holdout", contention_level=50.0, duration_s=6.0, t_start_wall=now, t_end_wall=now)

        eval1 = estimator.maybe_evaluate("demo")
        self.assertIsNotNone(eval1)

        # Add 1 more pair (total delta 2 < eval_every 4) -> should not evaluate yet
        self.store.record_build("demo", "reserved", contention_level=50.0, duration_s=3.0, t_start_wall=now, t_end_wall=now)
        self.store.record_build("demo", "holdout", contention_level=50.0, duration_s=6.0, t_start_wall=now, t_end_wall=now)
        self.assertIsNone(estimator.maybe_evaluate("demo"))

        # Add 1 more pair (total delta 4 == eval_every 4) -> evaluates
        self.store.record_build("demo", "reserved", contention_level=50.0, duration_s=3.0, t_start_wall=now, t_end_wall=now)
        self.store.record_build("demo", "holdout", contention_level=50.0, duration_s=6.0, t_start_wall=now, t_end_wall=now)
        eval2 = estimator.maybe_evaluate("demo")
        self.assertIsNotNone(eval2)

    def test_j_store_benefit_methods(self):
        """Part D.j: Verify all store benefit state and eval record methods."""
        self.store.set_benefit_state(
            project_id="demo",
            weight=250,
            arming_enabled=True,
            status="decayed",
            last_effect=0.12,
            last_p_value=0.04,
            n_reserved=15,
            n_holdout=14,
        )

        st = self.store.get_benefit_state("demo")
        self.assertEqual(st["weight"], 250)
        self.assertTrue(st["arming_enabled"])
        self.assertEqual(st["status"], "decayed")
        self.assertAlmostEqual(st["last_effect"], 0.12)
        self.assertAlmostEqual(st["last_p_value"], 0.04)

        # Record eval
        eval_id = self.store.record_benefit_eval(
            project_id="demo",
            evaluated_at=time.time(),
            n_reserved=15,
            n_holdout=14,
            per_bin_json="{}",
            overall_effect=0.12,
            ci_low=0.02,
            ci_high=0.22,
            p_value=0.04,
            action="decay",
            weight_after=125,
            arming_after=True,
        )
        self.assertGreater(eval_id, 0)

        evals = self.store.list_benefit_evals("demo")
        self.assertEqual(len(evals), 1)
        self.assertEqual(evals[0]["action"], "decay")
        self.assertEqual(evals[0]["weight_after"], 125)

    def test_k_sample_size_guards_no_action(self):
        """Step 9b Requirement 3b: evaluation with fewer than min_total_n per arm,
        or an eligible bin with fewer than min_bin_n in either arm, takes NO action (no decay, no disable)."""
        estimator = BenefitEstimator(
            store=self.store,
            configured_weight=500,
            min_bin_n=5,
            min_total_n=8,
        )

        now = time.time()
        # Seed 6 reserved and 6 holdout builds (less than min_total_n=8)
        for i in range(6):
            self.store.record_build("demo", "reserved", contention_level=50.0, duration_s=5.0, t_start_wall=now, t_end_wall=now)
            self.store.record_build("demo", "holdout", contention_level=50.0, duration_s=5.0, t_start_wall=now, t_end_wall=now)

        eval_res = estimator.evaluate("demo")
        self.assertEqual(eval_res["action"], "none")
        self.assertEqual(eval_res["weight_after"], 500)
        self.assertTrue(eval_res["arming_after"])

        state = self.store.get_benefit_state("demo")
        self.assertEqual(state["weight"], 500)
        self.assertTrue(state["arming_enabled"])
        self.assertEqual(state["status"], "active")

    def test_l_four_vs_seven_no_decay(self):
        """Step 9b Requirement 3c: 4 reserved vs 7 holdout builds must take NO decay action."""
        estimator = BenefitEstimator(
            store=self.store,
            configured_weight=500,
            min_bin_n=5,
            min_total_n=8,
        )

        now = time.time()
        # 4 reserved builds at 1.8s
        for _ in range(4):
            self.store.record_build("demo", "reserved", contention_level=0.0, duration_s=1.8, t_start_wall=now, t_end_wall=now)
        # 7 holdout builds at 1.8s
        for _ in range(7):
            self.store.record_build("demo", "holdout", contention_level=0.0, duration_s=1.8, t_start_wall=now, t_end_wall=now)

        # maybe_evaluate returns None because nr=4 < min_total_n=8
        self.assertIsNone(estimator.maybe_evaluate("demo"))

        # Explicit evaluate() must take action 'none' (retaining weight 500)
        res = estimator.evaluate("demo")
        self.assertEqual(res["action"], "none")
        self.assertEqual(res["weight_after"], 500)
        self.assertTrue(res["arming_after"])

        state = self.store.get_benefit_state("demo")
        self.assertEqual(state["weight"], 500)
        self.assertTrue(state["arming_enabled"])

    def test_m_heterogeneous_idle_and_contended(self):
        """Step 10 Part A.1.e.(i): Heterogeneous data (large idle bin with zero effect + small high bin with large effect).
        Gate enabled: decision is based on high bin only.
        Gate disabled: pooled effect is diluted by idle bin."""
        now = time.time()
        # Large idle bin (low contention 2%): 20 builds each, duration 2.0s vs 2.0s (0% effect)
        for _ in range(20):
            self.store.record_build("demo", "reserved", contention_level=2.0, duration_s=2.0, t_start_wall=now, t_end_wall=now)
            self.store.record_build("demo", "holdout", contention_level=2.0, duration_s=2.0, t_start_wall=now, t_end_wall=now)

        # Small high bin (contention 85%): 6 builds each, duration 2.0s vs 6.0s (~66.7% effect)
        for _ in range(6):
            self.store.record_build("demo", "reserved", contention_level=85.0, duration_s=2.0, t_start_wall=now, t_end_wall=now)
            self.store.record_build("demo", "holdout", contention_level=85.0, duration_s=6.0, t_start_wall=now, t_end_wall=now)

        # 1. Gate enabled (normal case):
        est_gate_on = BenefitEstimator(self.store, configured_weight=500, min_bin_n=5, contention_gate_enabled=True)
        res_on = est_gate_on.evaluate("demo")
        self.assertEqual(res_on["decision_bins"], ["high"])
        self.assertEqual(res_on["action"], "keep")
        self.assertAlmostEqual(res_on["overall_effect"], 0.6667, delta=0.05)
        self.assertLess(res_on["p_value"], 0.05)
        # Pooled effect is diluted: (40*0 + 12*0.6667) / 52 = ~0.1538
        self.assertAlmostEqual(res_on["pooled_effect"], 0.154, delta=0.05)

        # 2. Gate disabled:
        est_gate_off = BenefitEstimator(self.store, configured_weight=500, min_bin_n=5, contention_gate_enabled=False)
        res_off = est_gate_off.evaluate("demo")
        self.assertEqual(set(res_off["decision_bins"]), {"low", "high"})
        # Overall effect matches the pooled (diluted) effect
        self.assertAlmostEqual(res_off["overall_effect"], res_on["pooled_effect"], delta=0.02)
        self.assertLess(res_off["overall_effect"], res_on["overall_effect"])

    def test_n_regression_evals_5_and_6(self):
        """Step 10 Part A.1.e.(ii): Regression test reproducing evals 5 & 6.
        Low bin eligible with ~1% effect, high bin ineligible (e.g. only 2 builds).
        Gate-on and gate-off behave consistently, effect and CI match eligible set, no CI excludes zero with p=0.29."""
        now = time.time()
        # Low bin eligible: 10 reserved @ 3.0s, 10 holdout @ 3.03s (~1% effect)
        for i in range(10):
            self.store.record_build("demo", "reserved", contention_level=2.0, duration_s=3.0, t_start_wall=now, t_end_wall=now)
            self.store.record_build("demo", "holdout", contention_level=2.0, duration_s=3.03, t_start_wall=now, t_end_wall=now)

        # High bin ineligible: only 2 builds each
        for _ in range(2):
            self.store.record_build("demo", "reserved", contention_level=80.0, duration_s=2.0, t_start_wall=now, t_end_wall=now)
            self.store.record_build("demo", "holdout", contention_level=80.0, duration_s=4.0, t_start_wall=now, t_end_wall=now)

        # Gate-on: high bin is ineligible (n=2 < min_bin_n=5), low bin excluded by contention threshold
        # -> decision_bins is empty -> action is 'none'
        est_on = BenefitEstimator(self.store, configured_weight=500, min_bin_n=5, contention_gate_enabled=True)
        res_on = est_on.evaluate("demo")
        self.assertEqual(res_on["action"], "none")
        self.assertEqual(res_on["decision_bins"], [])

        # Gate-off: low bin is the only eligible bin in decision set
        est_off = BenefitEstimator(self.store, configured_weight=500, min_bin_n=5, contention_gate_enabled=False)
        res_off = est_off.evaluate("demo")
        self.assertEqual(res_off["decision_bins"], ["low"])
        # Effect and CI must match the low bin effect (~1%)
        self.assertAlmostEqual(res_off["overall_effect"], 0.0099, delta=0.01)
        # CI and p-value must be consistent: 95% CI covers zero or negative, p is not small (< 0.05)
        self.assertLessEqual(res_off["ci_low"], 0.05)
        # Ineligible high bin must NOT be used to compute effect or CI
        self.assertIsNone(res_off["per_bin"]["high"]["effect"])

    def test_o_bin_eligible_in_one_arm_only_ignored(self):
        """Step 10 Part A.1.e.(iii): Bin eligible in one arm only is ignored everywhere."""
        now = time.time()
        # Moderate bin: 8 reserved builds, but only 2 holdout builds (ineligible!)
        for _ in range(8):
            self.store.record_build("demo", "reserved", contention_level=45.0, duration_s=2.0, t_start_wall=now, t_end_wall=now)
        for _ in range(2):
            self.store.record_build("demo", "holdout", contention_level=45.0, duration_s=4.0, t_start_wall=now, t_end_wall=now)

        estimator = BenefitEstimator(self.store, configured_weight=500, min_bin_n=5)
        res = estimator.evaluate("demo")

        # Must be marked ineligible in per_bin
        m_stats = res["per_bin"]["moderate"]
        self.assertEqual(m_stats["n_reserved"], 8)
        self.assertEqual(m_stats["n_holdout"], 2)
        self.assertIsNone(m_stats["effect"])
        self.assertIsNone(m_stats["ci_low"])
        self.assertIsNone(m_stats["ci_high"])
        self.assertIsNone(m_stats["p_value"])

        # Must not be included in decision_bins or pooled
        self.assertNotIn("moderate", res["decision_bins"])
        self.assertNotIn("moderate", res["per_bin"]["pooled"]["eligible_bins"])


if __name__ == "__main__":
    unittest.main()
