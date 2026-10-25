"""Unit tests for harness delay generation, seed uniqueness, delay logging, and CV/SD assertions."""
import math
import random
import statistics
import unittest
from benchmarks.run_experiments import check_delay_distribution_assertion


class TestHarnessDelays(unittest.TestCase):
    def test_check_delay_distribution_assertion_manual_irregular_passes(self):
        # Uniform distribution U(0.5, 20) with n=10 has CV roughly ~ (19.5 / sqrt(12)) / 10.25 ~= 0.55 >= 0.4
        delays = [0.6, 2.3, 5.8, 8.1, 11.2, 14.5, 16.9, 18.2, 19.1, 3.4]
        # Should not raise
        check_delay_distribution_assertion("manual-irregular", delays)

    def test_check_delay_distribution_assertion_manual_irregular_fails_low_cv(self):
        # Identical or near-identical delays (CV < 0.4)
        delays = [12.96] * 10
        with self.assertRaises(AssertionError) as ctx:
            check_delay_distribution_assertion("manual-irregular", delays)
        self.assertIn("CV", str(ctx.exception))

        delays_tight = [10.0, 10.1, 9.9, 10.2, 9.8, 10.0, 10.1, 9.9, 10.0, 10.1]
        with self.assertRaises(AssertionError):
            check_delay_distribution_assertion("manual-irregular", delays_tight)

    def test_check_delay_distribution_assertion_auto_passes(self):
        # N(1.8, 0.1) jitter: stdev >= 0.03
        delays = [1.81, 1.75, 1.92, 1.68, 1.85, 1.79, 1.83, 1.71, 1.95, 1.80]
        # Should not raise
        check_delay_distribution_assertion("auto", delays)

    def test_check_delay_distribution_assertion_auto_fails_low_sd(self):
        # Constant or negligible jitter: stdev < 0.03
        delays = [1.80] * 10
        with self.assertRaises(AssertionError) as ctx:
            check_delay_distribution_assertion("auto", delays)
        self.assertIn("stdev", str(ctx.exception))

        delays_tiny_jitter = [1.801, 1.802, 1.799, 1.800, 1.801, 1.800, 1.799, 1.802, 1.801, 1.800]
        with self.assertRaises(AssertionError):
            check_delay_distribution_assertion("auto", delays_tiny_jitter)

    def test_seed_uniqueness_and_delay_s_distribution(self):
        # Simulate RNG calculation exactly as build_on_save.py does per cycle:
        # rng = random.Random(base_seed + build_counter)
        # delay = rng.uniform(0.5, 20.0)
        delays = []
        base_seed = 42
        for i in range(10):
            seed = base_seed + i
            rng = random.Random(seed)
            delay = rng.uniform(0.5, 20.0)
            delays.append(delay)

        self.assertEqual(len(set(delays)), 10, f"Delays must be distinct across seeds: {delays}")
        mean = statistics.mean(delays)
        sd = statistics.stdev(delays)
        cv = sd / mean
        self.assertGreaterEqual(cv, 0.40, f"CV {cv} should be >= 0.40")

    def test_auto_seed_uniqueness_and_delay_s_distribution(self):
        # Simulate RNG calculation for auto mode:
        # rng = random.Random(base_seed + build_counter)
        # delay = max(0.01, rng.gauss(1.8, 0.1))
        delays = []
        base_seed = 42
        for i in range(10):
            seed = base_seed + i
            rng = random.Random(seed)
            delay = max(0.01, rng.gauss(1.8, 0.1))
            delays.append(delay)

        self.assertEqual(len(set(delays)), 10, f"Delays must be distinct across seeds: {delays}")
        sd = statistics.stdev(delays)
        self.assertGreaterEqual(sd, 0.03, f"stdev {sd} should be >= 0.03s")


if __name__ == "__main__":
    unittest.main()
