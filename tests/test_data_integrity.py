"""Data integrity and verification tests for PreCompile experimental results.

Automated consistency checks:
1. N per cell equals the plan requirements.
2. Save-only scenarios (Scenario C and C') have zero builds.
3. Condition names in raw data match the plan (no fabricated condition names).
4. Scenario B delay distribution assertion (CV > 0.40).
5. Unboosted holdout builds under high load are slower than reserved builds.
"""

import json
import math
import statistics
import unittest
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS_DIR = REPO_ROOT / "benchmarks"


def assert_scenario_b_delay_distribution(delays: List[float], min_cv: float = 0.40) -> None:
    """Harness assertion for Scenario B irregular workload delays."""
    if not delays:
        raise AssertionError("Delay list is empty")
    if len(delays) < 5:
        raise AssertionError(f"Insufficient samples: {len(delays)} < 5")
    mean = statistics.mean(delays)
    stdev = statistics.stdev(delays)
    if mean <= 0:
        raise AssertionError(f"Mean delay must be positive, got {mean}")
    cv = stdev / mean
    if cv < min_cv:
        raise AssertionError(
            f"Scenario B delays lack required irregularity: observed CV={cv:.4f} < {min_cv:.2f} "
            f"(mean={mean:.2f}s, std={stdev:.4f}s, min={min(delays):.2f}s, max={max(delays):.2f}s). "
            f"Likely cause: fixed random seed per build."
        )


class TestDataIntegrity(unittest.TestCase):
    """Automated consistency checks across evaluation raw data and plan specifications."""

    def test_n_per_cell_matches_plan(self) -> None:
        """Requirement: N measured builds per cell in raw JSONL strictly equals plan requirements."""
        # Tier 1: 12 cells, 50 measured builds each
        t1_path = BENCHMARKS_DIR / "results_tier1" / "results.jsonl"
        self.assertTrue(t1_path.exists(), f"Missing {t1_path}")
        t1_records = [json.loads(l) for l in t1_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        t1_measured = [r for r in t1_records if not r.get("is_warmup", False)]
        self.assertEqual(len(t1_measured), 600, "Tier 1 should have 600 measured builds (12 cells * 50)")

        # Verify each of the 12 cells in Tier 1 has exactly 50
        for load in ["high", "moderate", "idle"]:
            for cond in ["c0-baseline", "c1-reactive", "c2-full", "c3-static"]:
                cnt = sum(1 for r in t1_measured if r["load_level"] == load and r["condition"] == cond)
                self.assertEqual(cnt, 50, f"Tier 1 ({load}, {cond}) count = {cnt} != 50")

        # Tier 2: Scenario B, 4 cells, 30 measured builds each
        t2_path = BENCHMARKS_DIR / "results_tier2" / "results.jsonl"
        self.assertTrue(t2_path.exists(), f"Missing {t2_path}")
        t2_records = [json.loads(l) for l in t2_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        t2_measured = [r for r in t2_records if not r.get("is_warmup", False) and r.get("scenario") == "B"]
        self.assertEqual(len(t2_measured), 120, "Tier 2 Scenario B should have 120 measured builds (4 cells * 30)")
        for cond in ["c0-baseline", "c1-reactive", "c2-full", "c3-static"]:
            cnt = sum(1 for r in t2_measured if r["condition"] == cond)
            self.assertEqual(cnt, 30, f"Tier 2 Scenario B ({cond}) count = {cnt} != 30")

        # Tier 3: Ablations
        t3_path = BENCHMARKS_DIR / "results_tier3" / "results.jsonl"
        self.assertTrue(t3_path.exists(), f"Missing {t3_path}")
        t3_records = [json.loads(l) for l in t3_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        t3_measured = [r for r in t3_records if not r.get("is_warmup", False)]
        t3_expected = {
            ("A", "high", "a4-fixedwin"): 50,
            ("A", "high", "a5-noholdout"): 50,
            ("A", "idle", "a3-nocont"): 50,
            ("B", "moderate", "a2-noconf"): 30,
        }
        for (sc, ld, cn), expected_n in t3_expected.items():
            cnt = sum(1 for r in t3_measured if r["scenario"] == sc and r["load_level"] == ld and r["condition"] == cn)
            self.assertEqual(cnt, expected_n, f"Tier 3 ({sc}, {ld}, {cn}) count = {cnt} != {expected_n}")

        # Post-hoc: A3' (50 builds)
        ph_path = BENCHMARKS_DIR / "results_posthoc" / "results.jsonl"
        self.assertTrue(ph_path.exists(), f"Missing {ph_path}")
        ph_records = [json.loads(l) for l in ph_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        ph_measured = [r for r in ph_records if not r.get("is_warmup", False)]
        a3_prime_cnt = sum(1 for r in ph_measured if r["condition"] == "a3-prime")
        self.assertEqual(a3_prime_cnt, 50, f"Posthoc a3-prime count = {a3_prime_cnt} != 50")

    def test_save_only_scenarios_have_no_builds(self) -> None:
        """Requirement: A save-only scenario (Scenario C and C') has zero builds in results.jsonl."""
        t2_path = BENCHMARKS_DIR / "results_tier2" / "results.jsonl"
        t2_records = [json.loads(l) for l in t2_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        scen_c_builds = [r for r in t2_records if r.get("scenario") == "C"]
        self.assertEqual(len(scen_c_builds), 0, "Scenario C is save-only and must have 0 build records in results.jsonl")

        ph_path = BENCHMARKS_DIR / "results_posthoc" / "results.jsonl"
        ph_records = [json.loads(l) for l in ph_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        scen_c_prime_builds = [r for r in ph_records if r.get("scenario") == "C'"]
        self.assertEqual(len(scen_c_prime_builds), 0, "Scenario C' is save-only and must have 0 build records in results.jsonl")

    def test_condition_names_match_plan(self) -> None:
        """Requirement: Condition names strictly match the pre-registered plan; no fabricated conditions."""
        valid_conditions = {
            "c0-baseline", "c1-reactive", "c2-full", "c3-static",
            "a2-noconf", "a3-nocont", "a4-fixedwin", "a5-noholdout",
            "a3-prime"
        }
        fabricated_conditions = {"a2-no-prior", "a4-no-watcher", "a5-no-feedback", "a6-no-cgroup"}

        observed_conditions = set()
        for dname in ["results_tier1", "results_tier2", "results_tier3", "results_posthoc"]:
            jp = BENCHMARKS_DIR / dname / "results.jsonl"
            if jp.exists():
                for l in jp.read_text(encoding="utf-8").splitlines():
                    if l.strip():
                        r = json.loads(l)
                        observed_conditions.add(r.get("condition"))

        # Assert all observed conditions are valid
        self.assertTrue(observed_conditions.issubset(valid_conditions), f"Unknown conditions found: {observed_conditions - valid_conditions}")
        # Assert none of the fabricated conditions exist
        self.assertEqual(len(observed_conditions.intersection(fabricated_conditions)), 0, "Fabricated condition names found in raw data")

    def test_delay_distribution_assertion_for_scenario_b(self) -> None:
        """Requirement: Delay-distribution assertion for Scenario B detects un-varied delays."""
        # 1. Test validator detects uniform random variations
        import random
        rng = random.Random(12345)
        good_delays = [rng.uniform(0.5, 20.0) for _ in range(30)]
        # Should not raise
        assert_scenario_b_delay_distribution(good_delays, min_cv=0.40)

        # 2. Test validator raises AssertionError on constant delays (like Tier 2 Scenario B run)
        bad_delays = [12.9659] * 30
        with self.assertRaises(AssertionError) as ctx:
            assert_scenario_b_delay_distribution(bad_delays, min_cv=0.40)
        self.assertIn("lack required irregularity", str(ctx.exception))

    def test_unboosted_holdout_builds_slower_than_reserved_at_high_load(self) -> None:
        """Requirement: Unboosted holdout builds at high load are slower than reserved ones."""
        # Tier 1 high load C2
        t1_path = BENCHMARKS_DIR / "results_tier1" / "results.jsonl"
        t1_records = [json.loads(l) for l in t1_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        c2_high = [r for r in t1_records if r["load_level"] == "high" and r["condition"] == "c2-full" and not r.get("is_warmup", False)]
        
        c2_res_walls = [r["build_wall_s"] for r in c2_high if r.get("claimed", False)]
        c2_hld_walls = [r["build_wall_s"] for r in c2_high if r.get("is_holdout", False)]
        
        self.assertGreater(len(c2_res_walls), 0)
        self.assertGreater(len(c2_hld_walls), 0)
        res_med = statistics.median(c2_res_walls)
        hld_med = statistics.median(c2_hld_walls)
        self.assertGreater(hld_med, res_med, f"Holdout builds ({hld_med:.2f}s) should be slower than reserved ({res_med:.2f}s)")

        # Tier 3 high load a4-fixedwin
        t3_path = BENCHMARKS_DIR / "results_tier3" / "results.jsonl"
        t3_records = [json.loads(l) for l in t3_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        a4_high = [r for r in t3_records if r["condition"] == "a4-fixedwin" and not r.get("is_warmup", False)]
        
        a4_res_walls = [r["build_wall_s"] for r in a4_high if r.get("claimed", False)]
        a4_hld_walls = [r["build_wall_s"] for r in a4_high if r.get("is_holdout", False)]
        
        self.assertGreater(len(a4_res_walls), 0)
        self.assertGreater(len(a4_hld_walls), 0)
        a4_res_med = statistics.median(a4_res_walls)
        a4_hld_med = statistics.median(a4_hld_walls)
        self.assertGreater(a4_hld_med, a4_res_med, f"A4 holdout builds ({a4_hld_med:.2f}s) should be slower than reserved ({a4_res_med:.2f}s)")


if __name__ == "__main__":
    unittest.main()
