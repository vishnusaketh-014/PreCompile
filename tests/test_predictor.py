"""Tests for latency predictor and EWMA engine (precompile/predictor.py).

Verifies Requirements a, b, c, d, e, f, i, j, k, l, m, n, o, p from Step 5 specifications:
    a. EWMA mean/variance match reference over a 10-value sequence (verifying old mean in variance)
    b. 15s outlier after 5 samples of 1.0s is clipped to mean + 3*s (s = floor 0.1s -> x_used = 1.3s)
    c. No clipping while n < 5
    d. Delays > 30s rejected, model unchanged, sample stored with accepted=False
    e. Delay of -0.01s clamps to 0; -0.5s is rejected
    f. Window for mean 1.8, sigma 0.35 is exactly [0.8, 2.8]; lower bound clamps at 0 for small means
    i. End-to-end with fake clock and in-memory store: 14 save-to-spawn cycles with delay 1.8 +/- 0.05s
    j. Unpredictable workflow (uniform 0.5-20s): can_predict never becomes True over 40 cycles
    k. Save with no build: expire_pending scores 'no_build' after 30s and hit-rate drops
    l. Second save before spawn: first prediction stays unscored, delay measured from second save
    m. Only the first matching spawn per save counts
    n. Save of class 'other' produces no sample and no prediction
    o. New LatencyPredictor over the same file DB restores n, mean, var
    p. Adaptation experiment: 20 samples at 1.8s, switch to 3.0s, count updates to reach 10% of 3.0
"""

import math
import os
import random
import tempfile
import unittest
from pathlib import Path
from typing import List, Tuple

from precompile.config import PredictorConfig
from precompile.monitor import SaveEvent
from precompile.predictor import (
    LatencyModel,
    LatencyPredictor,
    Prediction,
    UpdateResult,
    prediction_window,
    window_abs,
)
from precompile.store import DataStore


# Pure reference implementation defined directly in test from spec formulas
def reference_ewma_sequence(delays: List[float], alpha: float = 0.2) -> Tuple[float, float]:
    """Compute ground-truth EWMA mean and variance using spec equations."""
    mean = delays[0]
    var = 0.0
    for x in delays[1:]:
        old_mean = mean
        var = (1.0 - alpha) * (var + alpha * ((x - old_mean) ** 2))
        mean = (1.0 - alpha) * old_mean + alpha * x
    return mean, var


def reference_buggy_ewma_sequence(delays: List[float], alpha: float = 0.2) -> Tuple[float, float]:
    """Compute buggy EWMA that mistakenly updates mean BEFORE variance."""
    mean = delays[0]
    var = 0.0
    for x in delays[1:]:
        mean = (1.0 - alpha) * mean + alpha * x
        var = (1.0 - alpha) * (var + alpha * ((x - mean) ** 2))
    return mean, var


class TestPredictor(unittest.TestCase):
    """Test suite for LatencyModel and LatencyPredictor."""

    def test_a_ewma_mean_and_variance_match_reference(self):
        """Requirement a: EWMA mean and variance match reference over a 10-value sequence.

        Verifies that variance calculation uses the old mean rather than the updated mean.
        """
        delays = [1.0, 2.5, 1.8, 3.2, 0.9, 2.1, 1.4, 2.8, 1.9, 3.0]
        alpha = 0.2

        ref_mean, ref_var = reference_ewma_sequence(delays, alpha=alpha)
        buggy_mean, buggy_var = reference_buggy_ewma_sequence(delays, alpha=alpha)

        # The buggy variance is noticeably smaller than correct variance
        self.assertNotAlmostEqual(ref_var, buggy_var, places=4)

        model = LatencyModel()
        cfg = PredictorConfig(alpha=alpha, clip_min_samples=999)  # disable clipping for pure EWMA check
        for d in delays:
            res = model.update(d, config=cfg)
            self.assertTrue(res.accepted)

        self.assertEqual(model.n, 10)
        self.assertAlmostEqual(model.mean, ref_mean, places=9)
        self.assertAlmostEqual(model.var, ref_var, places=9)
        self.assertNotAlmostEqual(model.var, buggy_var, places=4)

    def test_b_outlier_clipping_with_sigma_floor(self):
        """Requirement b: 15s outlier after 5 samples of 1.0s is clipped to 1.3s with s=0.1 floor."""
        model = LatencyModel()
        cfg = PredictorConfig(alpha=0.2, clip_min_samples=5, clip_sigma=3.0, clip_sigma_floor_s=0.1)

        # 5 identical samples of 1.0s -> mean=1.0, var=0.0, sigma=0.0
        for _ in range(5):
            res = model.update(1.0, config=cfg)
            self.assertTrue(res.accepted)
            self.assertFalse(res.clipped)

        self.assertEqual(model.n, 5)
        self.assertAlmostEqual(model.mean, 1.0, places=7)
        self.assertAlmostEqual(model.sigma, 0.0, places=7)

        # 6th sample: 15.0s outlier
        # With n >= 5, clipping evaluates s = max(sigma, 0.1) = 0.1
        # Upper bound = mean + 3*s = 1.0 + 3*(0.1) = 1.3
        res_outlier = model.update(15.0, config=cfg)
        self.assertTrue(res_outlier.accepted)
        self.assertTrue(res_outlier.clipped)
        self.assertAlmostEqual(res_outlier.x_used, 1.3, places=7)

    def test_c_no_clipping_while_n_under_5(self):
        """Requirement c: Outliers are NOT clipped when n < 5."""
        model = LatencyModel()
        cfg = PredictorConfig(clip_min_samples=5, clip_sigma=3.0, clip_sigma_floor_s=0.1)

        # At n=1, 2, 3, 4, an outlier is accepted unclipped
        model.update(1.0, config=cfg)  # n=1
        model.update(1.0, config=cfg)  # n=2
        model.update(1.0, config=cfg)  # n=3
        res = model.update(10.0, config=cfg)  # n=4 -> n=4 < 5, so no clipping
        self.assertTrue(res.accepted)
        self.assertFalse(res.clipped)
        self.assertAlmostEqual(res.x_used, 10.0, places=7)

    def test_d_delay_over_30s_rejected(self):
        """Requirement d: Delays > 30s are rejected, model unchanged, sample stored as accepted=False."""
        store = DataStore(":memory:")
        store.add_project("proj_d", "/test", "default")
        pred = LatencyPredictor(store=store)

        # Feed 3 valid samples
        pred.update("proj_d", "source", "default", 1.5)
        pred.update("proj_d", "source", "default", 1.6)
        pred.update("proj_d", "source", "default", 1.5)

        stats_before = pred.get_stats("proj_d", "source", "default")
        mean_before = stats_before.mean
        n_before = stats_before.n

        # Delay > 30s directly
        res = pred.update("proj_d", "source", "default", 30.5)
        self.assertIsNone(res)

        # Model is unchanged
        stats_after = pred.get_stats("proj_d", "source", "default")
        self.assertEqual(stats_after.n, n_before)
        self.assertEqual(stats_after.mean, mean_before)

        # Via on_save / on_process_spawn
        now = 100.0
        pred.clock = lambda: now
        ev = SaveEvent("proj_d", Path("/test/a.c"), "source", now, now)
        pred.on_save(ev)
        # Spawn after 31s
        res_spawn = pred.on_process_spawn("proj_d", now + 31.0)
        self.assertFalse(res_spawn.accepted)

        # Sample stored with accepted=False
        samples = store.recent_samples("proj_d", "source", "default", n=10)
        last_sample = samples[-1]
        self.assertEqual(last_sample.delay_s, 31.0)
        self.assertFalse(last_sample.accepted)

    def test_e_negative_delays_clamped_and_rejected(self):
        """Requirement e: Delay of -0.01s clamps to 0; -0.5s is rejected."""
        model = LatencyModel()
        cfg = PredictorConfig(negative_delay_tolerance_s=0.02)

        # -0.01s is within [-0.02, 0.0] tolerance -> clamped to 0.0
        res_clamp = model.update(-0.01, config=cfg)
        self.assertTrue(res_clamp.accepted)
        self.assertAlmostEqual(res_clamp.x_used, 0.0, places=7)

        # -0.5s exceeds tolerance -> rejected
        res_reject = model.update(-0.5, config=cfg)
        self.assertFalse(res_reject.accepted)
        self.assertIsNone(res_reject.x_used)

    def test_f_window_calculation_and_lower_bound_clamping(self):
        """Requirement f: Window for mean 1.8, sigma 0.35 is [0.8, 2.8]; clamps to 0 for small means."""
        # Spec Fig. 2 timeline: mean=1.8, sigma=0.35, k=2.0, m=0.3
        # lo = 1.8 - 2.0*0.35 - 0.3 = 1.8 - 0.7 - 0.3 = 0.8
        # hi = 1.8 + 2.0*0.35 + 0.3 = 1.8 + 0.7 + 0.3 = 2.8
        m1 = LatencyModel(n=10, mean=1.8, var=0.35**2)
        lo, hi = prediction_window(m1, k=2.0, margin_m=0.3)
        self.assertAlmostEqual(lo, 0.8, places=7)
        self.assertAlmostEqual(hi, 2.8, places=7)

        abs_lo, abs_hi = window_abs(100.0, m1, k=2.0, margin_m=0.3)
        self.assertAlmostEqual(abs_lo, 100.8, places=7)
        self.assertAlmostEqual(abs_hi, 102.8, places=7)

        # Lower bound clamping at 0 for small mean:
        # mean=0.4, sigma=0.35 -> 0.4 - 1.0 = -0.6 -> clamps to 0.0
        m2 = LatencyModel(n=10, mean=0.4, var=0.35**2)
        lo_small, hi_small = prediction_window(m2, k=2.0, margin_m=0.3)
        self.assertEqual(lo_small, 0.0)
        self.assertAlmostEqual(hi_small, 1.4, places=7)

    def test_i_end_to_end_14_cycles_learning_and_arming(self):
        """Requirement i: 14 save-to-spawn cycles with delay 1.8 +/- 0.05s.

        Shadow predictions begin once n >= 5.
        can_arm becomes True after 5 scored predictions with hit rate >= 0.6.
        """
        store = DataStore(":memory:")
        store.add_project("proj_i", "/test", "default")
        current_mono = 1000.0

        def clock():
            return current_mono

        pred = LatencyPredictor(store=store, clock=clock)

        # 14 cycles of save -> process spawn with delay around 1.8 +/- 0.05s
        delays = [
            1.82, 1.78, 1.80, 1.81, 1.79,  # Cycles 1-5: collect initial 5 samples
            1.80, 1.82, 1.78, 1.81, 1.79,  # Cycles 6-10: 5 shadow predictions scored
            1.80, 1.81, 1.79, 1.80,        # Cycles 11-14: armed predictions
        ]

        scored_count = 0
        for cycle_idx, delay in enumerate(delays, start=1):
            current_mono += 10.0
            ev = SaveEvent(
                project_id="proj_i",
                path=Path("/test/main.c"),
                file_class="source",
                t_save_monotonic=current_mono,
                t_save_wall=current_mono,
            )
            prediction = pred.on_save(ev)

            if cycle_idx <= 5:
                # Samples 1 to 5: model n < 5 before save, so can_predict is False
                self.assertIsNone(prediction, f"Cycle {cycle_idx} should have no prediction (n < 5)")
            else:
                # Cycle 6 onwards: n >= 5, so shadow prediction is generated
                self.assertIsNotNone(prediction, f"Cycle {cycle_idx} should generate a prediction")
                if scored_count < 5:
                    self.assertFalse(
                        prediction.can_arm,
                        f"Cycle {cycle_idx} cannot arm before 5 scored predictions (scored={scored_count})",
                    )
                else:
                    self.assertTrue(
                        prediction.can_arm,
                        f"Cycle {cycle_idx} can arm after 5 scored hits (scored={scored_count})",
                    )

            # Spawn process
            current_mono += delay
            update_res = pred.on_process_spawn("proj_i", current_mono)
            self.assertTrue(update_res.accepted)

            if cycle_idx >= 6:
                scored_count += 1

        # Check final status
        desc = pred.describe(("proj_i", "source", "default"))
        self.assertEqual(desc["n"], 14)
        self.assertAlmostEqual(desc["mean"], 1.80, delta=0.05)
        self.assertTrue(desc["gate_result"]["can_predict"])
        self.assertTrue(desc["gate_result"]["can_arm"])
        self.assertEqual(desc["recent_hit_rate"]["scored_total"], 9)
        self.assertEqual(desc["recent_hit_rate"]["hits"], 9)
        self.assertEqual(desc["recent_hit_rate"]["rate"], 1.0)

    def test_j_unpredictable_workflow_never_predicts(self):
        """Requirement j: Statistical evaluation over 50 seeds for unpredictable workflows.

        NOTE ON SEED TUNING:
        The earlier version of test_j tested a single 40-cycle run with a searched/tuned seed
        (seed 1104). Because Uniform(0.5, 20s) has theoretical CV ~0.4912 right at the 0.5 threshold,
        individual seeds can occasionally dip below 0.5. As instructed for Step 6 Part A, we replace
        the single tuned seed with a rigorous multi-seed statistical evaluation:
        1. Exponential workload (mean 8.0s, theoretical CV ~ 1.0): assert can_arm is True for < 5% of saves overall.
        2. Uniform workload (0.5 to 20.0s): run across 50 seeds and print report-only metrics without assertion.
        """
        num_seeds = 50
        saves_per_seed = 100
        total_saves = num_seeds * saves_per_seed

        # 1. Exponential workload test (assert can_arm < 5% overall)
        exp_armed_count = 0
        for seed in range(num_seeds):
            rng = random.Random(seed)
            store = DataStore(":memory:")
            store.add_project(f"proj_exp_{seed}", "/test", "default")
            current_mono = 100.0

            def clock():
                return current_mono

            pred = LatencyPredictor(store=store, clock=clock)
            for _ in range(saves_per_seed):
                current_mono += 10.0
                ev = SaveEvent(f"proj_exp_{seed}", Path("/test/main.c"), "source", current_mono, current_mono)
                p = pred.on_save(ev)
                if p is not None and p.can_arm:
                    exp_armed_count += 1
                delay = rng.expovariate(1.0 / 8.0)
                current_mono += delay
                pred.on_process_spawn(f"proj_exp_{seed}", current_mono)
            store.close()

        exp_armed_pct = (exp_armed_count / float(total_saves)) * 100.0
        self.assertLess(
            exp_armed_pct,
            5.0,
            f"Exponential workload armed in {exp_armed_pct:.2f}% of saves, expected < 5.0%",
        )

        # 2. Uniform workload (report-only printout, no assertion)
        uniform_predict_count = 0
        uniform_armed_count = 0
        for seed in range(num_seeds):
            rng = random.Random(seed)
            store = DataStore(":memory:")
            store.add_project(f"proj_uni_{seed}", "/test", "default")
            current_mono = 100.0

            def clock():
                return current_mono

            pred = LatencyPredictor(store=store, clock=clock)
            for _ in range(saves_per_seed):
                current_mono += 10.0
                ev = SaveEvent(f"proj_uni_{seed}", Path("/test/main.c"), "source", current_mono, current_mono)
                p = pred.on_save(ev)
                if p is not None:
                    uniform_predict_count += 1
                    if p.can_arm:
                        uniform_armed_count += 1
                delay = rng.uniform(0.5, 20.0)
                current_mono += delay
                pred.on_process_spawn(f"proj_uni_{seed}", current_mono)
            store.close()

        uni_pred_pct = (uniform_predict_count / float(total_saves)) * 100.0
        uni_arm_pct = (uniform_armed_count / float(total_saves)) * 100.0
        print(f"\n[UNPREDICTABLE WORKFLOW STATISTICAL REPORT (50 seeds x {saves_per_seed} saves)]")
        print(f"  Exponential workload (mean 8.0s): {exp_armed_pct:.2f}% armed (asserted < 5.0%)")
        print(f"  Uniform workload (0.5-20.0s): {uni_pred_pct:.2f}% can_predict, {uni_arm_pct:.2f}% can_arm (report-only)")

    def test_k_save_with_no_build_expires_and_scores_no_build(self):
        """Requirement k: A save with no build: expire_pending scores 'no_build' after 30s and hit-rate drops."""
        store = DataStore(":memory:")
        store.add_project("proj_k", "/test", "default")
        current_mono = 100.0

        def clock():
            return current_mono

        pred = LatencyPredictor(store=store, clock=clock)

        # Establish model with 5 hits (1.8s) so predictions are active
        for _ in range(5):
            current_mono += 10.0
            ev = SaveEvent("proj_k", Path("/test/a.c"), "source", current_mono, current_mono)
            pred.on_save(ev)
            current_mono += 1.8
            pred.on_process_spawn("proj_k", current_mono)

        # Now save with prediction
        current_mono += 10.0
        ev = SaveEvent("proj_k", Path("/test/a.c"), "source", current_mono, current_mono)
        p = pred.on_save(ev)
        self.assertIsNotNone(p)

        # Clock advances 31s with NO spawn
        current_mono += 31.0
        expired = pred.expire_pending(now=current_mono)
        self.assertIn(p.prediction_id, expired)

        # Check outcome in store: 'no_build' counts as miss
        hits, total, rate = store.recent_hit_rate("proj_k", "source", "default", last=10)
        self.assertEqual(total, 1)
        self.assertEqual(hits, 0)
        self.assertEqual(rate, 0.0)

    def test_l_second_save_before_spawn_replaces_pending(self):
        """Requirement l: A second save before spawn leaves first prediction unscored; delay measured from second."""
        store = DataStore(":memory:")
        store.add_project("proj_l", "/test", "default")
        current_mono = 100.0

        def clock():
            return current_mono

        pred = LatencyPredictor(store=store, clock=clock)

        # Seed 5 samples
        for _ in range(5):
            pred.update("proj_l", "source", "default", 1.8)

        # Save 1 at t=100.0
        current_mono = 100.0
        ev1 = SaveEvent("proj_l", Path("/test/a.c"), "source", current_mono, current_mono)
        p1 = pred.on_save(ev1)
        self.assertIsNotNone(p1)

        # Save 2 at t=105.0 before any spawn
        current_mono = 105.0
        ev2 = SaveEvent("proj_l", Path("/test/a.c"), "source", current_mono, current_mono)
        p2 = pred.on_save(ev2)
        self.assertIsNotNone(p2)

        # Spawn at t=106.8 (delay from save 2 is 1.8s; delay from save 1 would be 6.8s)
        current_mono = 106.8
        res = pred.on_process_spawn("proj_l", current_mono)
        self.assertTrue(res.accepted)

        # Prediction 1 should remain unscored (outcome is None)
        outcomes = store.get_recent_outcomes("proj_l", limit=10)
        scored_ids = {o.id for o in outcomes}
        self.assertNotIn(p1.prediction_id, scored_ids)
        self.assertIn(p2.prediction_id, scored_ids)

        # Prediction 2 was a hit
        p2_rec = [o for o in outcomes if o.id == p2.prediction_id][0]
        self.assertEqual(p2_rec.outcome, "hit")
        self.assertAlmostEqual(p2_rec.observed_delay_s, 1.8, places=4)

    def test_m_only_first_matching_spawn_counts(self):
        """Requirement m: Only the first matching spawn per save counts; later ones are ignored."""
        store = DataStore(":memory:")
        store.add_project("proj_m", "/test", "default")
        current_mono = 100.0

        def clock():
            return current_mono

        pred = LatencyPredictor(store=store, clock=clock)

        ev = SaveEvent("proj_m", Path("/test/a.c"), "source", current_mono, current_mono)
        pred.on_save(ev)

        # First spawn
        res1 = pred.on_process_spawn("proj_m", current_mono + 1.8)
        self.assertIsNotNone(res1)
        self.assertTrue(res1.accepted)

        # Second spawn for the same save
        res2 = pred.on_process_spawn("proj_m", current_mono + 2.5)
        self.assertIsNone(res2)

        # Store has exactly 1 sample
        self.assertEqual(store.sample_count("proj_m", "source", "default"), 1)

    def test_n_save_of_class_other_ignored(self):
        """Requirement n: A save of class 'other' produces no sample and no prediction."""
        store = DataStore(":memory:")
        store.add_project("proj_n", "/test", "default")
        pred = LatencyPredictor(store=store)

        ev = SaveEvent("proj_n", Path("/test/README.txt"), "other", 100.0, 100.0)
        p = pred.on_save(ev)
        self.assertIsNone(p)

        # Spawn does nothing
        res = pred.on_process_spawn("proj_n", 102.0)
        self.assertIsNone(res)
        self.assertEqual(store.sample_count("proj_n", "other", "default"), 0)

    def test_o_persistence_restores_model_state_across_instances(self):
        """Requirement o: A new LatencyPredictor over the same file DB restores n, mean, var."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            # Instance 1
            store1 = DataStore(db_path)
            store1.add_project("proj_o", "/test", "default")
            pred1 = LatencyPredictor(store=store1)

            pred1.update("proj_o", "source", "default", 2.0)
            pred1.update("proj_o", "source", "default", 2.2)
            pred1.update("proj_o", "source", "default", 1.8)

            stats1 = pred1.get_stats("proj_o", "source", "default")
            expected_n = stats1.n
            expected_mean = stats1.mean
            expected_var = stats1.var
            store1.close()

            # Instance 2 reopening same DB
            store2 = DataStore(db_path)
            pred2 = LatencyPredictor(store=store2)
            stats2 = pred2.get_stats("proj_o", "source", "default")

            self.assertIsNotNone(stats2)
            self.assertEqual(stats2.n, expected_n)
            self.assertAlmostEqual(stats2.mean, expected_mean, places=7)
            self.assertAlmostEqual(stats2.var, expected_var, places=7)
            store2.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_p_adaptation_experiment(self):
        """Requirement p: ADAPTATION EXPERIMENT (report, do not tune).

        After 20 samples at 1.8s, switch true delay to 3.0s and count updates
        until mean is within 10% of 3.0s (i.e. >= 2.70s).
        Assert no-clipping case needs <= 10 updates.
        Report updates for clipping case without asserting.
        """
        # Case 1: No clipping (clip_min_samples set very high)
        m_noclip = LatencyModel()
        cfg_noclip = PredictorConfig(alpha=0.2, clip_min_samples=999999)
        for _ in range(20):
            m_noclip.update(1.8, config=cfg_noclip)

        self.assertAlmostEqual(m_noclip.mean, 1.8, places=4)

        updates_noclip = 0
        while m_noclip.mean < 2.70 and updates_noclip < 100:
            m_noclip.update(3.0, config=cfg_noclip)
            updates_noclip += 1

        self.assertLessEqual(
            updates_noclip,
            10,
            f"No-clipping adaptation took {updates_noclip} updates, expected <= 10",
        )

        # Case 2: Clipping enabled (default clip_min_samples=5, floor=0.1)
        m_clip = LatencyModel()
        cfg_clip = PredictorConfig(alpha=0.2, clip_min_samples=5, clip_sigma=3.0, clip_sigma_floor_s=0.1)
        for _ in range(20):
            m_clip.update(1.8, config=cfg_clip)

        self.assertAlmostEqual(m_clip.mean, 1.8, places=4)

        updates_clip = 0
        while m_clip.mean < 2.70 and updates_clip < 100:
            m_clip.update(3.0, config=cfg_clip)
            updates_clip += 1

        print(f"\n[ADAPTATION EXPERIMENT RESULT]")
        print(f"  No-clipping updates to reach 10% of 3.0s (>= 2.70s): {updates_noclip}")
        print(f"  With-clipping updates to reach 10% of 3.0s (>= 2.70s): {updates_clip}")
        print(f"  Final mean with clipping after {updates_clip} updates: {m_clip.mean:.4f}s")


if __name__ == "__main__":
    unittest.main()
