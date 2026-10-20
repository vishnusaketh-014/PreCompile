"""Pure-Python statistical tests and utilities for PreCompile benefit estimation.

Stage Mapping: Stage 9 (Benefit Estimation & Priority Feedback Control).
Provides:
    - mann_whitney_u: Non-parametric two-sample rank-sum test with tie correction.
    - bootstrap_relative_effect_ci: Stratified bootstrap confidence intervals for relative effect.
    - stouffer_z_combine: Stouffer's meta-analytic Z-score combination across bins.
    - median, percentile: Basic descriptive statistics.
"""

import math
import random
from typing import Dict, List, Optional, Sequence, Tuple


def median(data: Sequence[float]) -> float:
    """Compute the median of a non-empty sequence."""
    if not data:
        return 0.0
    s = sorted(data)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def percentile(data: Sequence[float], p: float) -> float:
    """Compute p-th percentile [0..100] using linear interpolation."""
    if not data:
        return 0.0
    s = sorted(data)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(s[int(k)])
    d0 = s[int(f)] * (c - k)
    d1 = s[int(c)] * (k - f)
    return float(d0 + d1)


def _exact_mann_whitney_p_noties(n1: int, n2: int, u_val: float) -> float:
    """Exact two-sided p-value for Mann-Whitney U without ties using dynamic programming.

    Let c(m, n, u) be the number of subsets of size m from {1..m+n} that sum to u + m*(m+1)/2.
    Recurrence: c(m, n, u) = c(m-1, n, u - n) + c(m, n-1, u).
    """
    total_u = n1 * n2
    u_int = int(round(u_val))
    mean_u = total_u / 2.0
    u_diff = abs(u_int - mean_u)

    # DP table: dp[i][j][k] where i in 0..n1, j in 0..n2, k in 0..total_u
    # We can compute dp column by column or 2D: dp[i][k] for subsets of size i from 1..m+n
    # Standard count: distribution of rank sums
    # dp[k] = number of partitions of k into at most m parts, each <= n.
    # dp table of size (total_u + 1)
    dp = [0] * (total_u + 1)
    dp[0] = 1
    for item in range(1, n1 + n2 + 1):
        # We can either pick item or not pick item
        # But this is partitions into at most n1 parts.
        pass

    # A straightforward exact DP for c(m, n, u):
    # c(i, j, u) = number of partitions into i parts each <= j with sum u.
    # Base cases: c(0, j, 0) = 1; c(i, 0, 0) = 1 for i=0 else 0.
    # We can compute dp[i][u]: number of partitions into i parts from 1..j
    table = [[0] * (total_u + 1) for _ in range(n1 + 1)]
    table[0][0] = 1

    for j in range(1, n2 + 1):
        for i in range(1, n1 + 1):
            for u in range(j, total_u + 1):
                table[i][u] += table[i - 1][u - j]

    # Recurrence using standard generating function:
    # Gaussian polynomials / q-binomial coefficient [n1+n2 choose n1]_q
    # Poly P_0 = [1]
    poly = [1]
    for k in range(n1):
        # Multiply by (1 - q^(n2 + 1 + k)) / (1 - q^(1 + k))
        # Equivalently: build DP directly:
        pass

    # For n1 + n2 <= 20, math.comb(n1+n2, n1) <= 184,756.
    # Direct enumeration or DP of rank sums:
    # rank_dp[i][s] = number of combinations of i ranks from {1..N} with sum s.
    N = n1 + n2
    max_s = N * (N + 1) // 2
    r_dp = [0] * (max_s + 1)
    r_dp[0] = 1
    # 2D table: counts[i][s] = subsets of size i
    counts = [[0] * (max_s + 1) for _ in range(n1 + 1)]
    counts[0][0] = 1
    for r in range(1, N + 1):
        for i in range(min(r, n1), 0, -1):
            row_prev = counts[i - 1]
            row_curr = counts[i]
            for s in range(max_s, r - 1, -1):
                row_curr[s] += row_prev[s - r]

    offset = n1 * (n1 + 1) // 2
    total_ways = math.comb(N, n1)
    extreme_count = 0
    for s in range(offset, offset + total_u + 1):
        num_ways = counts[n1][s]
        if num_ways > 0:
            u_cand = s - offset
            if abs(u_cand - mean_u) >= u_diff - 1e-9:
                extreme_count += num_ways

    return min(1.0, max(0.0, extreme_count / total_ways))


def _exact_mann_whitney_p_ties(rank_list: List[float], n1: int, n2: int, u_obs: float) -> float:
    """Exact permutation test for Mann-Whitney U when ties are present and total combinations <= 250,000."""
    import itertools
    total_ways = math.comb(n1 + n2, n1)
    offset = n1 * (n1 + 1) / 2.0
    mean_u = n1 * n2 / 2.0
    u_diff = abs(u_obs - mean_u)
    extreme_count = 0

    for comb in itertools.combinations(rank_list, n1):
        u_cand = sum(comb) - offset
        if abs(u_cand - mean_u) >= u_diff - 1e-9:
            extreme_count += 1

    return min(1.0, max(0.0, extreme_count / total_ways))


def mann_whitney_u(x: Sequence[float], y: Sequence[float]) -> Tuple[float, float, float, float]:
    """Pure-Python Mann-Whitney U test with exact small-sample evaluation and tie-corrected normal approximation.

    For small samples (n1 + n2 <= 20), computes the exact two-sided p-value:
    - Without ties: uses exact rank-sum dynamic programming over partitions.
    - With ties: uses exact permutation of rank arrangements (comb(n1+n2, n1) <= 184,756).
    For larger samples (n1 + n2 > 20), uses the standard tie-corrected normal approximation.

    Args:
        x: First sample values.
        y: Second sample values.

    Returns:
        (u1, u_min, z_score, p_value) where p_value is two-sided.
    """
    n1 = len(x)
    n2 = len(y)
    if n1 == 0 or n2 == 0:
        return 0.0, 0.0, 0.0, 1.0

    # Combine observations with sample origin tag (1 for x, 2 for y)
    combined = [(float(val), 1) for val in x] + [(float(val), 2) for val in y]
    combined.sort(key=lambda t: t[0])

    # Assign ranks with tie handling (average rank for equal values)
    ranks: List[Tuple[float, int]] = []
    i = 0
    total = len(combined)
    tie_counts: List[int] = []

    while i < total:
        j = i
        while j < total - 1 and combined[j][0] == combined[j + 1][0]:
            j += 1
        num_ties = j - i + 1
        avg_rank = (i + 1 + j + 1) / 2.0
        if num_ties > 1:
            tie_counts.append(num_ties)
        for k in range(i, j + 1):
            ranks.append((avg_rank, combined[k][1]))
        i = j + 1

    # Sum ranks for sample 1 (x)
    r1 = sum(r for r, sample_id in ranks if sample_id == 1)
    u1 = r1 - (n1 * (n1 + 1)) / 2.0
    u2 = (n1 * n2) - u1
    u = min(u1, u2)

    mean_u = (n1 * n2) / 2.0

    # Tie correction for variance
    n = n1 + n2
    tie_term = sum((t**3 - t) for t in tie_counts)
    if n > 1:
        tie_correction = 1.0 - (tie_term / (n**3 - n))
    else:
        tie_correction = 1.0

    var_u = (n1 * n2 / 12.0) * ((n + 1) * tie_correction)
    std_u = math.sqrt(max(1e-9, var_u))

    z = (u1 - mean_u) / std_u

    # Exact vs Asymptotic p-value calculation
    if n <= 20:
        if not tie_counts:
            p = _exact_mann_whitney_p_noties(n1, n2, u1)
        else:
            rank_values = [r for r, _ in ranks]
            p = _exact_mann_whitney_p_ties(rank_values, n1, n2, u1)
    else:
        # Two-sided p-value from standard normal CDF with tie correction
        p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2.0))))

    return float(u1), float(u), float(z), float(min(1.0, max(0.0, p)))


def bootstrap_relative_effect_ci(
    reserved_by_bin: Dict[str, Sequence[float]],
    holdout_by_bin: Dict[str, Sequence[float]],
    n_resamples: int = 1000,
    alpha: float = 0.05,
    seed: int = 42,
    eligible_bins: Optional[Sequence[str]] = None,
) -> Tuple[float, float, float]:
    """Compute overall relative speedup effect and bootstrap confidence interval.

    Per-bin effect is defined as: 1.0 - (median(reserved) / median(holdout)).
    Overall effect is the weighted average across bins, weighted by (n_reserved + n_holdout).

    Args:
        reserved_by_bin: Map of bin_name -> list of reserved build durations.
        holdout_by_bin: Map of bin_name -> list of holdout build durations.
        n_resamples: Number of bootstrap iterations (default 1000).
        alpha: Two-sided significance level (default 0.05 for 95% CI).
        seed: Random seed for deterministic reproducibility.
        eligible_bins: Optional subset of bin names to restrict calculation to.

    Returns:
        (overall_effect, ci_low, ci_high)
    """
    if eligible_bins is not None:
        active_bins = [
            b for b in eligible_bins
            if len(reserved_by_bin.get(b, [])) > 0 and len(holdout_by_bin.get(b, [])) > 0
        ]
    else:
        active_bins = [
            b for b in reserved_by_bin
            if len(reserved_by_bin.get(b, [])) > 0 and len(holdout_by_bin.get(b, [])) > 0
        ]

    if not active_bins:
        return 0.0, 0.0, 0.0

    # Compute observed overall effect
    total_weights = 0.0
    weighted_effect_sum = 0.0

    for b in active_bins:
        r_list = reserved_by_bin[b]
        h_list = holdout_by_bin[b]
        weight = len(r_list) + len(h_list)
        med_r = median(r_list)
        med_h = median(h_list)
        bin_effect = 1.0 - (med_r / med_h) if med_h > 0 else 0.0
        weighted_effect_sum += weight * bin_effect
        total_weights += weight

    observed_overall = weighted_effect_sum / total_weights if total_weights > 0 else 0.0

    # Resample with replacement within each eligible bin
    rng = random.Random(seed)
    boot_effects: List[float] = []

    for _ in range(n_resamples):
        w_sum = 0.0
        e_sum = 0.0
        for b in active_bins:
            r_pool = reserved_by_bin[b]
            h_pool = holdout_by_bin[b]
            nr = len(r_pool)
            nh = len(h_pool)
            w = nr + nh

            # Resample
            r_samp = [r_pool[rng.randrange(nr)] for _ in range(nr)]
            h_samp = [h_pool[rng.randrange(nh)] for _ in range(nh)]

            m_r = median(r_samp)
            m_h = median(h_samp)
            b_effect = 1.0 - (m_r / m_h) if m_h > 0 else 0.0
            e_sum += w * b_effect
            w_sum += w

        boot_effects.append(e_sum / w_sum if w_sum > 0 else 0.0)

    boot_effects.sort()
    low_idx = percentile(boot_effects, (alpha / 2.0) * 100.0)
    high_idx = percentile(boot_effects, (1.0 - alpha / 2.0) * 100.0)

    return float(observed_overall), float(low_idx), float(high_idx)


def stouffer_z_combine(p_values_and_weights: Sequence[Tuple[float, float]]) -> float:
    """Combine p-values across independent bins using Stouffer's weighted Z-method.

    Args:
        p_values_and_weights: Sequence of (p_value, weight) tuples.

    Returns:
        Combined p-value in [0, 1].
    """
    valid = [(p, w) for p, w in p_values_and_weights if w > 0]
    if not valid:
        return 1.0

    z_sum = 0.0
    w_sq_sum = 0.0

    for p, w in valid:
        # Clamp p to avoid infinite z
        p_clamped = min(0.999999999999999, max(1e-15, p))
        # Standard one-sided Z = Phi^-1(1 - p)
        z = _norm_inv_cdf(1.0 - p_clamped)
        z_sum += w * z
        w_sq_sum += w * w

    if w_sq_sum <= 0:
        return 1.0

    combined_z = z_sum / math.sqrt(w_sq_sum)
    # One-sided p-value: P(Z >= combined_z)
    combined_p = 1.0 - 0.5 * (1.0 + math.erf(combined_z / math.sqrt(2.0)))
    return float(min(1.0, max(0.0, combined_p)))


def _norm_inv_cdf(p: float) -> float:
    """Rational approximation for inverse standard normal CDF (probit)."""
    # Abramowitz and Stegun formula 26.2.23
    p = min(0.999999999999999, max(1e-15, p))
    if p < 0.5:
        return -_norm_inv_cdf(1.0 - p)

    t = math.sqrt(-2.0 * math.log(1.0 - p))
    c0 = 2.515517
    c1 = 0.802853
    c2 = 0.010328
    d1 = 1.432788
    d2 = 0.189269
    d3 = 0.001308
    num = c0 + (c1 + c2 * t) * t
    den = 1.0 + ((d1 + d2 * t) * t + d3 * t * t) * t
    return t - (num / den)
