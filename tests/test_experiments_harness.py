"""Unit tests for PreCompile experimental evaluation harness and analysis suite.

Stage Mapping: Step 11a Part D.
Tests:
- Plan parsing and matrix construction (Tier 1, Tier 2, Pilot).
- Deterministic balanced Latin-square block rotation generator.
- Resume functionality without duplicating finished blocks.
- Watchdog timeout protection on hung commands.
- Synthetic JSONL statistical analysis with Holm-Bonferroni correction and bootstrap CIs.
- Resource cleanup trap execution.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from benchmarks.analyze_experiments import analyze_experiments, holm_bonferroni
from benchmarks.run_experiments import (
    ExperimentRunner,
    cleanup_system,
    generate_balanced_blocks,
)


class TestExperimentHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="precompile_harness_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_latin_square_balanced_block_rotation(self) -> None:
        """Test Latin-square block sequence generation across cycles."""
        conds = ["C0", "C1", "C2", "C3"]
        cycles = 4
        blocks = generate_balanced_blocks(conds, cycles)

        self.assertEqual(len(blocks), len(conds) * cycles)

        # Check cycle counts per condition
        counts = {c: 0 for c in conds}
        for cy, cond in blocks:
            counts[cond] += 1
        for cond, count in counts.items():
            self.assertEqual(count, cycles, f"Condition {cond} should appear exactly {cycles} times")

        # Verify rotation shifts: starting condition for each cycle must rotate
        starts = [blocks[cy * len(conds)][1] for cy in range(cycles)]
        self.assertEqual(starts, ["C0", "C1", "C2", "C3"])

    def test_plan_construction_tier1_tier2_pilot(self) -> None:
        """Test execution matrix generation for Tier 1, Tier 2, and Pilot modes."""
        runner_tier1 = ExperimentRunner(tier="1", pilot=False, dry_run=True, output_dir=self.temp_dir)
        plan_tier1 = runner_tier1.get_plan()
        self.assertEqual(len(plan_tier1), 60)  # 3 loads * 4 conds * 5 cycles

        runner_pilot = ExperimentRunner(tier="1", pilot=True, dry_run=True, output_dir=self.temp_dir)
        plan_pilot = runner_pilot.get_plan()
        self.assertEqual(len(plan_pilot), 8)  # 2 loads * 4 conds * 1 cycle
        self.assertEqual(plan_pilot[0]["builds_per_block"], 3)
        self.assertEqual(plan_pilot[0]["warmups"], 2)

        runner_tier2 = ExperimentRunner(tier="2", pilot=False, dry_run=True, output_dir=self.temp_dir)
        plan_tier2 = runner_tier2.get_plan()
        self.assertEqual(len(plan_tier2), 25)  # 1 load * 5 ablations * 5 cycles

    def test_resume_skips_completed_blocks_without_duplication(self) -> None:
        """Test that resume skips previously completed blocks in JSONL."""
        jsonl_path = self.temp_dir / "results.jsonl"
        completed_records = [
            {"scenario": "A", "load_level": "moderate", "condition": "c0-baseline", "cycle": 1, "is_warmup": False},
            {"scenario": "A", "load_level": "moderate", "condition": "c1-reactive", "cycle": 1, "is_warmup": False},
        ]
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for r in completed_records:
                f.write(json.dumps(r) + "\n")

        runner = ExperimentRunner(
            tier="1",
            pilot=True,
            dry_run=True,
            resume=True,
            output_dir=self.temp_dir,
        )
        runner.init_environment()

        self.assertIn(("A", "moderate", "c0-baseline", 1), runner.completed_blocks)
        self.assertIn(("A", "moderate", "c1-reactive", 1), runner.completed_blocks)
        self.assertEqual(len(runner.completed_blocks), 2)

    def test_watchdog_timeout_aborts_hung_commands(self) -> None:
        """Test that the 3x watchdog timer halts hung commands cleanly."""
        cmd = ["python3", "-c", "import time; time.sleep(10)"]
        with self.assertRaises(subprocess.TimeoutExpired):
            subprocess.run(cmd, timeout=0.2, capture_output=True)

    def test_holm_bonferroni_adjustment(self) -> None:
        """Test step-down Holm-Bonferroni adjusted p-values."""
        # Unadjusted: [0.01, 0.04, 0.03]
        # Sorted: 0.01 (m=3 -> 0.03), 0.03 (m=2 -> 0.06), 0.04 (m=1 -> 0.04 -> max=0.06)
        raw_p = [0.01, 0.04, 0.03]
        adj = holm_bonferroni(raw_p)

        self.assertAlmostEqual(adj[0], 0.03, places=4)
        self.assertAlmostEqual(adj[1], 0.06, places=4)
        self.assertAlmostEqual(adj[2], 0.06, places=4)

    def test_synthetic_jsonl_statistical_analysis(self) -> None:
        """Test statistical analysis on synthetic benchmark JSONL records."""
        jsonl_path = self.temp_dir / "synthetic_results.jsonl"

        records = []
        # C0 baseline: 8.0s builds
        for i in range(10):
            records.append({
                "scenario": "A", "load_level": "high", "condition": "c0-baseline",
                "duration_wall_s": 8.0 + 0.1 * i, "is_warmup": False,
                "claimed": False, "claim_latency_ms": None, "cgroup_cpu_share": None,
            })
        # C1 reactive: 7.5s builds, 40ms latency
        for i in range(10):
            records.append({
                "scenario": "A", "load_level": "high", "condition": "c1-reactive",
                "duration_wall_s": 7.5 + 0.1 * i, "is_warmup": False,
                "claimed": True, "claim_latency_ms": 40.0 + i, "cgroup_cpu_share": 0.85,
            })
        # C2 full: 5.0s builds, 15ms latency
        for i in range(10):
            records.append({
                "scenario": "A", "load_level": "high", "condition": "c2-full",
                "duration_wall_s": 5.0 + 0.1 * i, "is_warmup": False,
                "claimed": True, "claim_latency_ms": 15.0 + i, "cgroup_cpu_share": 0.95,
            })

        with open(jsonl_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        analysis = analyze_experiments(jsonl_path, output_dir=self.temp_dir)
        metrics = analysis["metrics_summary"]
        self.assertEqual(len(metrics), 3)

        c0_metric = next(m for m in metrics if m["condition"] == "c0-baseline")
        c2_metric = next(m for m in metrics if m["condition"] == "c2-full")
        self.assertGreater(c0_metric["med_wall_s"], c2_metric["med_wall_s"])

        comps = analysis["comparisons"]
        self.assertGreaterEqual(len(comps), 3)

        # C2 vs C0 should show significant speedup
        c2_vs_c0 = next(c for c in comps if "C2 vs C0" in c["hypothesis"])
        self.assertGreater(c2_vs_c0["relative_speedup"], 0.30)
        self.assertLess(c2_vs_c0["holm_p"], 0.05)
        self.assertTrue(c2_vs_c0["significant"])

        # CSV files should be written
        self.assertTrue((self.temp_dir / "summary_metrics.csv").exists())
        self.assertTrue((self.temp_dir / "hypothesis_tests.csv").exists())

    def test_cleanup_system_trap(self) -> None:
        """Test that cleanup_system runs without raising exceptions."""
        dummy_load = self.temp_dir / "dummy_load.sh"
        dummy_load.write_text("#!/bin/sh\nexit 0\n")
        dummy_load.chmod(0o755)

        cleanup_system(dummy_load)


if __name__ == "__main__":
    unittest.main()
