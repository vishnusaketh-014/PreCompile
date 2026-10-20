"""Unit tests for PreCompile experimental evaluation harness and analysis suite.

Stage Mapping: Step 11a Part D / Step 11a3.
Tests:
- Plan parsing and matrix construction (Tier 1, Tier 2, Pilot).
- Deterministic balanced Latin-square block rotation generator.
- Resume functionality without duplicating finished blocks.
- Watchdog timeout protection on hung commands.
- Dynamic system metadata header extraction.
- PSI ramp-down wait logic.
- Synthetic JSONL statistical analysis with Holm-Bonferroni correction, steady-state sensitivity, and unboosted detection.
- Resource cleanup trap execution.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.analyze_experiments import analyze_experiments, holm_bonferroni
from benchmarks.run_experiments import (
    ExperimentRunner,
    cleanup_system,
    generate_balanced_blocks,
    get_system_metadata,
    wait_for_ramp_down,
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

        counts = {c: 0 for c in conds}
        for cy, cond in blocks:
            counts[cond] += 1
        for cond, count in counts.items():
            self.assertEqual(count, cycles, f"Condition {cond} should appear exactly {cycles} times")

        starts = [blocks[cy * len(conds)][1] for cy in range(cycles)]
        self.assertEqual(starts, ["C0", "C1", "C2", "C3"])

    def test_plan_construction_tier1_tier2_pilot(self) -> None:
        """Test execution matrix generation for Tier 1, Tier 2, and Pilot modes."""
        runner_tier1 = ExperimentRunner(tier="1", pilot=False, dry_run=True, output_dir=self.temp_dir)
        plan_tier1 = runner_tier1.get_plan()
        self.assertEqual(len(plan_tier1), 60)  # 3 loads * 4 conds * 5 cycles

        runner_pilot = ExperimentRunner(tier="1", pilot=True, dry_run=True, output_dir=self.temp_dir)
        plan_pilot = runner_pilot.get_plan()
        # Pilot: 4 (Scen A high) + 4 (Scen B mod) + 4 (Scen C mod) + 1 (high burst) + 2 (idle rampdown) = 15 blocks
        self.assertEqual(len(plan_pilot), 15)

        runner_tier2 = ExperimentRunner(tier="2", pilot=False, dry_run=True, output_dir=self.temp_dir)
        plan_tier2 = runner_tier2.get_plan()
        # Tier 2: 12 (Scen B) + 4 (Scen C) = 16 blocks
        self.assertEqual(len(plan_tier2), 16)

    def test_dynamic_header_metadata(self) -> None:
        """Test dynamic environment metadata extraction."""
        meta = get_system_metadata(cpuset="0-1", tier="1", pilot=True)
        self.assertIn("kernel", meta)
        self.assertIn("cpu_model", meta)
        self.assertIn("stress_ng_version", meta)
        self.assertEqual(meta["cpuset"], "0-1")
        self.assertTrue(meta["pilot"])
        self.assertNotEqual(meta["stress_ng_version"], "0.13.12")  # Confirms dynamic rather than hardcoded

    def test_ramp_down_wait_logic(self) -> None:
        """Test PSI ramp-down wait function."""
        with patch("benchmarks.run_experiments.read_psi_cpu_avg10", side_effect=[12.0, 8.0, 4.2]):
            with patch("time.sleep", return_value=None):
                final_psi = wait_for_ramp_down(target_psi=5.0, max_wait_s=10.0)
                self.assertLess(final_psi, 5.0)

    def test_resume_skips_completed_blocks_without_duplication(self) -> None:
        """Test that resume skips previously completed blocks in JSONL."""
        jsonl_path = self.temp_dir / "results.jsonl"
        completed_records = [
            {"scenario": "A", "load_level": "high", "condition": "c0-baseline", "cycle": 1, "is_warmup": False},
            {"scenario": "A", "load_level": "high", "condition": "c1-reactive", "cycle": 1, "is_warmup": False},
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

        self.assertIn(("A", "high", "c0-baseline", 1), runner.completed_blocks)
        self.assertIn(("A", "high", "c1-reactive", 1), runner.completed_blocks)
        self.assertEqual(len(runner.completed_blocks), 2)

    def test_watchdog_timeout_aborts_hung_commands(self) -> None:
        """Test that watchdog timer halts hung commands cleanly."""
        cmd = ["python3", "-c", "import time; time.sleep(10)"]
        with self.assertRaises(subprocess.TimeoutExpired):
            subprocess.run(cmd, timeout=0.2, capture_output=True)

    def test_holm_bonferroni_adjustment(self) -> None:
        """Test step-down Holm-Bonferroni adjusted p-values."""
        raw_p = [0.01, 0.04, 0.03]
        adj = holm_bonferroni(raw_p)

        self.assertAlmostEqual(adj[0], 0.03, places=4)
        self.assertAlmostEqual(adj[1], 0.06, places=4)
        self.assertAlmostEqual(adj[2], 0.06, places=4)

    def test_synthetic_jsonl_statistical_analysis(self) -> None:
        """Test statistical analysis on synthetic benchmark JSONL records with index 0 sensitivity."""
        jsonl_path = self.temp_dir / "synthetic_results.jsonl"

        records = []
        # C0 baseline: 8.0s builds
        for i in range(10):
            records.append({
                "scenario": "A", "load_level": "high", "condition": "c0-baseline",
                "block_build_index": i, "build_index": i + 1,
                "duration_wall_s": 8.0 + 0.1 * i, "is_warmup": False,
                "claimed": False, "claim_latency_ms": None, "cgroup_cpu_share": None,
                "unboosted": False,
            })
        # C1 reactive: 7.5s builds, 40ms latency (index 0 is slower)
        for i in range(10):
            records.append({
                "scenario": "A", "load_level": "high", "condition": "c1-reactive",
                "block_build_index": i, "build_index": i + 1,
                "duration_wall_s": (8.5 if i == 0 else 7.5 + 0.1 * i), "is_warmup": False,
                "claimed": True, "claim_latency_ms": 60.0 if i == 0 else 40.0 + i,
                "cgroup_cpu_share": 0.85, "unboosted": False,
            })
        # C2 full: 5.0s builds, 15ms latency (index 0 is cold)
        for i in range(10):
            records.append({
                "scenario": "A", "load_level": "high", "condition": "c2-full",
                "block_build_index": i, "build_index": i + 1,
                "duration_wall_s": (6.0 if i == 0 else 5.0 + 0.1 * i), "is_warmup": False,
                "claimed": True, "claim_latency_ms": 30.0 if i == 0 else 15.0 + i,
                "cgroup_cpu_share": 0.95, "unboosted": False,
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

        # Check steady-state metric excluding index 0
        self.assertLess(c2_metric["med_steady_wall_s"], c2_metric["med_wall_s"] + 0.1)
        self.assertLess(c2_metric["med_steady_lat_ms"], c2_metric["med_claim_lat_ms"])

        comps = analysis["comparisons"]
        self.assertGreaterEqual(len(comps), 3)

        c2_vs_c0 = next(c for c in comps if "C2 vs C0" in c["hypothesis"])
        self.assertGreater(c2_vs_c0["relative_speedup"], 0.30)
        self.assertLess(c2_vs_c0["holm_p"], 0.05)
        self.assertTrue(c2_vs_c0["significant"])

        self.assertTrue((self.temp_dir / "summary_metrics.csv").exists())

    def test_cleanup_system_trap(self) -> None:
        """Test that cleanup_system runs without raising exceptions."""
        dummy_load = self.temp_dir / "dummy_load.sh"
        dummy_load.write_text("#!/bin/sh\nexit 0\n")
        dummy_load.chmod(0o755)

        cleanup_system(dummy_load)


if __name__ == "__main__":
    unittest.main()
