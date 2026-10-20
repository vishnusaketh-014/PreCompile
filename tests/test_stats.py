"""Unit tests for Stage 9 statistical tests (precompile/stats.py).

Tests:
    - mann_whitney_u against edge cases, identical distributions, strictly separated distributions, ties.
    - bootstrap_relative_effect_ci with known speedup and zero speedup.
    - stouffer_z_combine combining significant and non-significant p-values.
    - median and percentile helpers.
"""

import math
import unittest

from precompile.stats import (
    bootstrap_relative_effect_ci,
    mann_whitney_u,
    median,
    percentile,
    stouffer_z_combine,
)


class TestStats(unittest.TestCase):
    """Test suite for precompile/stats.py."""

    def test_median_and_percentile(self):
        # Odd length
        self.assertEqual(median([5, 1, 3]), 3.0)
        # Even length
        self.assertEqual(median([1, 2, 3, 4]), 2.5)
        # Empty
        self.assertEqual(median([]), 0.0)

        # Percentile
        data = [10.0, 20.0, 30.0, 40.0, 50.0]
        self.assertAlmostEqual(percentile(data, 0.0), 10.0)
        self.assertAlmostEqual(percentile(data, 50.0), 30.0)
        self.assertAlmostEqual(percentile(data, 100.0), 50.0)

    def test_mann_whitney_u_strictly_separated(self):
        """Strictly separated distributions should yield minimum U=0 and p < 0.05."""
        x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        y = [11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0]
        u1, u, z, p = mann_whitney_u(x, y)
        self.assertEqual(u, 0.0)
        self.assertLess(p, 0.001)

    def test_mann_whitney_u_identical(self):
        """Identical distributions should yield u ~ n1*n2/2 and p close to 1."""
        x = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
        y = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
        u1, u, z, p = mann_whitney_u(x, y)
        self.assertEqual(u, 18.0)
        self.assertGreater(p, 0.9)

    def test_mann_whitney_u_with_ties(self):
        """Ties are handled properly with average rank assignment."""
        x = [5.0, 5.0, 5.0, 5.0]
        y = [5.0, 5.0, 5.0, 5.0]
        u1, u, z, p = mann_whitney_u(x, y)
        self.assertEqual(u, 8.0)
        self.assertGreater(p, 0.9)

    def test_mann_whitney_u_empty(self):
        self.assertEqual(mann_whitney_u([], [1, 2, 3]), (0.0, 0.0, 0.0, 1.0))

    def test_bootstrap_relative_effect_ci_speedup(self):
        """Verify bootstrap confidence interval when reserved is ~50% faster than holdout."""
        # Reserved ~ 2.0s, Holdout ~ 4.0s -> effect ~ 50%
        reserved_by_bin = {"high": [1.9, 2.0, 2.1, 1.8, 2.2, 2.0, 1.9, 2.1]}
        holdout_by_bin = {"high": [3.8, 4.0, 4.2, 3.9, 4.1, 4.0, 3.9, 4.1]}

        effect, ci_lo, ci_hi = bootstrap_relative_effect_ci(
            reserved_by_bin, holdout_by_bin, n_resamples=500, seed=42
        )
        self.assertAlmostEqual(effect, 0.50, delta=0.05)
        self.assertGreater(ci_lo, 0.40)
        self.assertLess(ci_hi, 0.60)

    def test_bootstrap_relative_effect_ci_zero_benefit(self):
        """Verify bootstrap confidence interval when reserved equals holdout."""
        reserved_by_bin = {"low": [3.0, 3.1, 2.9, 3.0, 3.2, 2.8, 3.1, 3.0]}
        holdout_by_bin = {"low": [3.0, 2.9, 3.1, 3.0, 2.8, 3.2, 3.0, 3.1]}

        effect, ci_lo, ci_hi = bootstrap_relative_effect_ci(
            reserved_by_bin, holdout_by_bin, n_resamples=500, seed=42
        )
        self.assertAlmostEqual(effect, 0.0, delta=0.05)
        self.assertLess(ci_lo, 0.05)
        self.assertGreater(ci_hi, -0.05)

    def test_stouffer_z_combine(self):
        # Combining two highly significant p-values
        combined_p = stouffer_z_combine([(0.01, 10.0), (0.02, 10.0)])
        self.assertLess(combined_p, 0.005)

        # Combining non-significant p-values
        combined_null = stouffer_z_combine([(0.60, 5.0), (0.70, 5.0)])
        self.assertGreater(combined_null, 0.50)

        # Empty
        self.assertEqual(stouffer_z_combine([]), 1.0)

    def test_mann_whitney_u_exact_small_samples(self):
        """Verify exact small-sample p-value without ties against hand-computed values.

        For x = [1, 2] and y = [3, 4, 5]:
          n1 = 2, n2 = 3. Total combinations = comb(5, 2) = 10.
          Rank sum for x is 1 + 2 = 3 -> U1 = 3 - 2*3/2 = 0.
          U2 = 6, U = 0, mean_U = 3.0, |U - mean_U| = 3.0.
          Possible subsets of size 2 from {1,2,3,4,5}:
            {1,2}: U=0, diff=3.0 (extreme)
            {1,3}: U=1, diff=2.0
            {1,4}: U=2, diff=1.0
            {1,5}: U=3, diff=0.0
            {2,3}: U=2, diff=1.0
            {2,4}: U=3, diff=0.0
            {2,5}: U=4, diff=1.0
            {3,4}: U=4, diff=1.0
            {3,5}: U=5, diff=2.0
            {4,5}: U=6, diff=3.0 (extreme)
          Extreme combinations where |U - 3| >= 3 are {1,2} and {4,5} -> 2 / 10 = 0.200.
        """
        u1, u, z, p = mann_whitney_u([1.0, 2.0], [3.0, 4.0, 5.0])
        self.assertEqual(u, 0.0)
        self.assertAlmostEqual(p, 0.20, places=4)

        # For x = [1, 2, 3, 4] and y = [5, 6, 7]:
        #   n1 = 4, n2 = 3. Total combinations = comb(7, 4) = 35.
        #   Strict separation: x is completely less than y -> U = 0.
        #   Extreme combinations are U = 0 (1 way: {1,2,3,4}) and U = 12 (1 way: {4,5,6,7}).
        #   Two-sided p-value = 2 / 35 = 0.057142857...
        u1, u, z, p = mann_whitney_u([1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0])
        self.assertEqual(u, 0.0)
        self.assertAlmostEqual(p, 2.0 / 35.0, places=5)

    def test_mann_whitney_u_exact_ties(self):
        """Verify exact small-sample permutation test when ties are present."""
        # x = [1.0, 2.0], y = [2.0, 3.0]
        # Combined: [1.0, 2.0, 2.0, 3.0]
        # Ranks: [1.0, 2.5, 2.5, 4.0]
        u1, u, z, p = mann_whitney_u([1.0, 2.0], [2.0, 3.0])
        self.assertGreater(p, 0.2)
        self.assertLessEqual(p, 1.0)


if __name__ == "__main__":
    unittest.main()
