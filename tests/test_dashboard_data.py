"""Tests for PreCompile Dashboard read-only data layer.

Verifies:
1. Strict read-only SQLite guarantees (mode=ro prevents any write/modify operations).
2. Clean empty DB handling (returns graceful 'no data yet' structures without exceptions).
3. Benchmark JSONL parsing, contention binning, and statistical metric calculations.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard.reader import DashboardDataReader
from precompile.store import DataStore


class TestDashboardDataLayer(unittest.TestCase):
    """Test suite covering dashboard data reader requirements."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "test_telemetry.db"
        self.results_dir = self.temp_path / "benchmarks"
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import gc
        gc.collect()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_read_only_guarantee(self) -> None:
        """Verify that DashboardDataReader opens SQLite strictly read-only and refuses writes."""
        # Initialize schema via standard DataStore
        with DataStore(self.db_path) as store:
            store.add_project("proj_test", str(self.temp_path), "default")

        reader = DashboardDataReader(db_path=self.db_path, results_dir=self.results_dir)
        conn = reader.connect_ro()

        # Reading succeeds
        cur = conn.execute("SELECT id FROM projects")
        rows = cur.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "proj_test")

        # Any write attempt MUST raise OperationalError (readonly database)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("INSERT INTO projects (id, root_path, tool_profile_name, enabled, created_at) VALUES ('bad', '/tmp', 'default', 1, 0.0)")

        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM projects WHERE id = 'proj_test'")

        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("UPDATE projects SET enabled = 0 WHERE id = 'proj_test'")

        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE evil (x INT)")

        conn.close()

    def test_empty_db_graceful_handling(self) -> None:
        """Verify that an empty database returns honest 'no data yet' structures."""
        # Initialize empty DB
        with DataStore(self.db_path) as store:
            pass

        reader = DashboardDataReader(db_path=self.db_path, results_dir=self.results_dir)

        # 1. Overview
        overview = reader.get_live_overview()
        self.assertFalse(overview["empty"])
        self.assertEqual(overview["state_machine"]["current_state"], "Idle")
        self.assertEqual(overview["tiles"]["confidence"]["value"], "N/A")
        self.assertEqual(overview["tiles"]["confidence"]["subtext"], "no data yet")
        self.assertEqual(overview["tiles"]["delay"]["value"], "N/A")
        self.assertEqual(overview["tiles"]["delay"]["subtext"], "no data yet")
        self.assertEqual(overview["tiles"]["weight"]["value"], "100")
        self.assertEqual(len(overview["transitions"]), 0)

        # 2. Distributions
        dist = reader.get_reserved_vs_holdout()
        self.assertTrue(dist["empty"])
        self.assertIsNone(dist["summary"]["speedup_pct"])
        self.assertIsNone(dist["summary"]["p_value"])
        self.assertEqual(dist["reserved"]["stats"]["n"], 0)
        self.assertEqual(dist["holdout"]["stats"]["n"], 0)

    def test_live_overview_state_machine_and_tiles(self) -> None:
        """Verify state machine inference, metric tiles, and transition logs."""
        with DataStore(self.db_path) as store:
            store.add_project("proj_alpha", str(self.temp_path), "default")
            # Seed delay samples
            for d in [1.80, 1.82, 1.79, 1.81, 1.80]:
                store.record_sample("proj_alpha", "source", "default", delay_s=d, accepted=True)
            store.save_model_state(("proj_alpha", "source", "default"), n=5, mean=1.80, var=0.0001)

            # Record reservation
            store.record_reservation({
                "id": "res-001",
                "project_id": "proj_alpha",
                "final_state": "ARMED",
                "weight": 500,
                "armed_at": 100.0,
                "window_lo_abs_s": 101.5,
                "window_hi_abs_s": 102.5,
                "group_name": "precompile-res-001",
            })

        reader = DashboardDataReader(db_path=self.db_path, results_dir=self.results_dir)
        overview = reader.get_live_overview()

        # State should be Armed because active reservation has final_state ARMED and is unclosed
        self.assertEqual(overview["state_machine"]["current_state"], "Armed")
        self.assertEqual(overview["tiles"]["weight"]["value"], "500")
        self.assertEqual(overview["tiles"]["delay"]["value"], "1.80s")
        self.assertTrue(overview["tiles"]["confidence"]["value"].endswith("%"))
        self.assertIn("CV:", overview["tiles"]["confidence"]["subtext"])
        self.assertGreater(len(overview["transitions"]), 0)
        self.assertEqual(overview["transitions"][0]["to_state"], "Armed")

    def test_benchmark_jsonl_replay_parsing(self) -> None:
        """Verify parsing raw benchmark results.jsonl and computing statistical distributions."""
        tier1_dir = self.results_dir / "results_tier1"
        tier1_dir.mkdir(parents=True, exist_ok=True)
        jsonl_file = tier1_dir / "results.jsonl"

        # Generate sample trial records (10 reserved, 10 holdout)
        records = []
        for i in range(10):
            # Reserved builds: ~2.0s
            records.append({
                "timestamp": 1000.0 + i,
                "tier": "1",
                "scenario": "A",
                "load_level": "moderate",
                "condition": "c2-full",
                "is_warmup": False,
                "duration_wall_s": 1.95 + 0.05 * (i % 3),
                "contention_level": 12.0,
                "claimed": True,
                "is_holdout": False,
                "cgroup_cpu_share": 500,
            })
            # Holdout builds: ~4.5s
            records.append({
                "timestamp": 2000.0 + i,
                "tier": "1",
                "scenario": "A",
                "load_level": "moderate",
                "condition": "c2-full",
                "is_warmup": False,
                "duration_wall_s": 4.50 + 0.1 * (i % 3),
                "contention_level": 12.0,
                "claimed": False,
                "is_holdout": True,
                "cgroup_cpu_share": 100,
            })

        with open(jsonl_file, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        reader = DashboardDataReader(db_path=self.db_path, results_dir=self.results_dir)
        dist = reader.get_reserved_vs_holdout(scenario="A", load_level="moderate")

        self.assertFalse(dist["empty"])
        self.assertEqual(dist["reserved"]["stats"]["n"], 10)
        self.assertEqual(dist["holdout"]["stats"]["n"], 10)
        self.assertAlmostEqual(dist["reserved"]["stats"]["median"], 2.00, delta=0.1)
        self.assertAlmostEqual(dist["holdout"]["stats"]["median"], 4.55, delta=0.1)

        # Speedup should be > 50%
        self.assertGreater(dist["summary"]["speedup_pct"], 50.0)
        # Mann-Whitney U test should be statistically significant (p < 0.05)
        self.assertIsNotNone(dist["summary"]["p_value"])
        self.assertLess(dist["summary"]["p_value"], 0.005)
        self.assertTrue(dist["summary"]["statistically_significant"])

        # Contention bins: moderate should have both
        mod_bin = dist["contention_bins"]["moderate"]
        self.assertEqual(mod_bin["n_reserved"], 10)
        self.assertEqual(mod_bin["n_holdout"], 10)
        self.assertGreater(mod_bin["effect_pct"], 50.0)

    def test_prediction_timeline_calculation(self) -> None:
        """Verify View 2 prediction timeline window, spawn pips, and prediction error."""
        with DataStore(self.db_path) as store:
            store.add_project("proj_timeline", str(self.temp_path), "default")
            # Record prediction
            pred_id = store.record_prediction(
                project_id="proj_timeline",
                file_class="source",
                tool_profile="default",
                t_save_wall=100.0,
                window_lo=1.40,
                window_hi=2.20,
                armed=True,
                holdout=False,
            )
            store.set_prediction_decision(pred_id, "would_arm", 12.0, 0.45, 0.08)
            store.score_prediction(pred_id, observed_delay_s=1.85, outcome="hit")

        reader = DashboardDataReader(db_path=self.db_path, results_dir=self.results_dir)
        timeline = reader.get_prediction_timeline(project_id="proj_timeline")

        self.assertFalse(timeline["empty"])
        self.assertGreater(timeline["total_cycles"], 0)
        cycle = timeline["cycles"][0]
        self.assertEqual(cycle["window_lo"], 1.40)
        self.assertEqual(cycle["window_hi"], 2.20)
        self.assertAlmostEqual(cycle["predicted_spawn"], 1.80, places=2)
        self.assertAlmostEqual(cycle["actual_spawn"], 1.85, places=2)
        self.assertAlmostEqual(cycle["prediction_error_s"], 0.05, places=2)
        self.assertEqual(cycle["outcome"], "hit")

    def test_benefit_estimator_state_and_history(self) -> None:
        """Verify View 4 benefit estimator state, evaluations, and weight history."""
        with DataStore(self.db_path) as store:
            store.add_project("proj_benefit", str(self.temp_path), "default")
            store.set_benefit_state(
                project_id="proj_benefit",
                weight=500,
                arming_enabled=True,
                status="effective",
                last_effect=0.55,
                last_p_value=0.001,
                n_reserved=20,
                n_holdout=20,
            )
            store.record_benefit_eval(
                project_id="proj_benefit",
                evaluated_at=1000.0,
                n_reserved=20,
                n_holdout=20,
                per_bin_json=json.dumps({"moderate": {"effect": 0.55, "p_value": 0.001}}),
                overall_effect=0.55,
                ci_low=0.45,
                ci_high=0.65,
                p_value=0.001,
                action="step_up",
                weight_after=500,
                arming_after=True,
            )

        reader = DashboardDataReader(db_path=self.db_path, results_dir=self.results_dir)
        benefit = reader.get_benefit_estimator(project_id="proj_benefit")

        self.assertFalse(benefit["empty"])
        self.assertEqual(benefit["state"]["weight"], 500)
        self.assertTrue(benefit["state"]["arming_enabled"])
        self.assertEqual(len(benefit["evaluations"]), 1)
        self.assertEqual(benefit["evaluations"][0]["action"], "step_up")
        self.assertEqual(benefit["evaluations"][0]["weight_after"], 500)

    def test_safety_governor_status_and_invariants(self) -> None:
        """Verify View 5 safety governor budget gauge and kill switch detection."""
        pause_flag = self.temp_path / "test_kill.pause"
        reader = DashboardDataReader(
            db_path=self.db_path, results_dir=self.results_dir, pause_file=pause_flag
        )

        # 1. Clear kill switch
        safety = reader.get_safety_governor()
        self.assertFalse(safety["kill_switch"]["engaged"])
        self.assertIn("DISENGAGED", safety["kill_switch"]["status"])
        self.assertEqual(safety["budget"]["limit_seconds"], 900.0)

        # 2. Engage kill switch
        pause_flag.write_text("paused", encoding="utf-8")
        safety_paused = reader.get_safety_governor()
        self.assertTrue(safety_paused["kill_switch"]["engaged"])
        self.assertIn("ENGAGED", safety_paused["kill_switch"]["status"])

    def test_experiments_evaluation_integrity_and_criteria(self) -> None:
        """Verify View 6 experimental results, honest criteria disclosure (S1-S7), and ablations."""
        reader = DashboardDataReader(db_path=self.db_path, results_dir=self.results_dir)
        exp = reader.get_experiments_evaluation()

        criteria = {c["criterion"]: c for c in exp["criteria_status"]}
        self.assertEqual(criteria["S1: Contended Speedup"]["status"], "PASS")
        # Ensure honest failure reporting
        self.assertEqual(criteria["S3: Claim Latency Advantage"]["status"], "FAIL")
        self.assertEqual(criteria["S4: Precision on Irregular Work"]["status"], "INVALID AS RUN")
        self.assertEqual(criteria["S5: Monitoring Overhead"]["status"], "FAIL")

        # Verify Table 2a conditions matrix exists
        self.assertGreater(len(exp["conditions_matrix"]), 10)
        # Verify ablations exist
        self.assertGreater(len(exp["ablations"]), 4)

    def test_decision_log_stream_and_filtering(self) -> None:
        """Verify View 7 decision log querying and category filtering."""
        with DataStore(self.db_path) as store:
            store.add_project("proj_dec", str(self.temp_path), "default")
            p1 = store.record_prediction(
                project_id="proj_dec",
                file_class="source",
                tool_profile="default",
                t_save_wall=100.0,
                window_lo=1.40,
                window_hi=2.20,
                armed=True,
                holdout=False,
            )
            store.set_prediction_decision(p1, "would_arm", 15.0, 0.50, 0.08)

            p2 = store.record_prediction(
                project_id="proj_dec",
                file_class="source",
                tool_profile="default",
                t_save_wall=110.0,
                window_lo=1.40,
                window_hi=2.20,
                armed=False,
                holdout=False,
            )
            store.set_prediction_decision(p2, "abstain_no_contention", 2.0, 0.0, 0.0)

        reader = DashboardDataReader(db_path=self.db_path, results_dir=self.results_dir)

        # Query all
        all_dec = reader.get_decision_log(category="All")
        self.assertEqual(all_dec["count"], 2)

        # Filter armed
        armed_dec = reader.get_decision_log(category="armed")
        self.assertEqual(armed_dec["count"], 1)
        self.assertEqual(armed_dec["decisions"][0]["decision"], "would_arm")

        # Filter abstain
        abstain_dec = reader.get_decision_log(category="abstain")
        self.assertEqual(abstain_dec["count"], 1)
        self.assertEqual(abstain_dec["decisions"][0]["decision"], "abstain_no_contention")

    def test_replay_manifest_and_trials(self) -> None:
        """Verify Demo Mode replay manifest discovery and trials parsing."""
        reader = DashboardDataReader(db_path=self.db_path, results_dir=self.results_dir)
        manifest = reader.get_replay_manifest()
        self.assertIn("datasets", manifest)
        self.assertGreater(len(manifest["datasets"]), 0)

    def test_compare_data_high_contention_speedup(self) -> None:
        """Verify Hero Compare feature: Scenario A High load shows statistically significant speedup."""
        reader = DashboardDataReader(db_path=self.db_path, results_dir=Path("benchmarks/results"))
        data = reader.get_compare_data(scenario="A", load_level="high")

        self.assertEqual(data["scenario"], "A")
        self.assertEqual(data["load_level"], "high")
        self.assertIn("without", data)
        self.assertIn("with_precompile", data)
        self.assertIn("reactive_only", data)
        self.assertIn("speedup", data)
        self.assertIn("overlay_timeline", data)

        # Build durations
        w_out_med = data["without"]["median_s"]
        w_in_med = data["with_precompile"]["median_s"]
        self.assertIsNotNone(w_out_med)
        self.assertIsNotNone(w_in_med)
        self.assertGreater(w_out_med, w_in_med)

        # Speedup statistics
        speedup = data["speedup"]
        self.assertGreater(speedup["relative_pct"], 50.0)
        self.assertTrue(speedup["is_significant"])
        self.assertLess(speedup["p_value"], 0.001)
        self.assertIn("+", speedup["status_badge"])

        # Timeline events
        events = data["overlay_timeline"]["events"]
        event_names = [e["name"] for e in events]
        self.assertIn("Save Event", event_names)
        self.assertIn("Reservation Applied", event_names)
        self.assertIn("Actual Spawn", event_names)
        self.assertIn("WITH Finish", event_names)
        self.assertIn("WITHOUT Finish", event_names)

    def test_compare_data_idle_honesty_no_difference(self) -> None:
        """Verify Hero Compare feature: Scenario A Idle load honestly reports No Significant Difference."""
        reader = DashboardDataReader(db_path=self.db_path, results_dir=Path("benchmarks/results"))
        data = reader.get_compare_data(scenario="A", load_level="idle")

        speedup = data["speedup"]
        self.assertEqual(speedup["status_badge"], "No Significant Difference")
        self.assertFalse(speedup["is_significant"])
        self.assertGreater(speedup["p_value"], 0.05)
        self.assertIn("zero harmful interference", speedup["honest_explanation"].lower())

    def test_compare_data_empty_db_graceful(self) -> None:
        """Verify Compare screen graceful degradation when no records exist."""
        empty_results = self.temp_path / "empty_benchmarks"
        empty_results.mkdir(parents=True, exist_ok=True)
        reader = DashboardDataReader(db_path=self.temp_path / "nonexistent.db", results_dir=empty_results)
        data = reader.get_compare_data(scenario="Z", load_level="idle")

        self.assertEqual(data["without"]["count"], 0)
        self.assertEqual(data["with_precompile"]["count"], 0)
        self.assertEqual(data["speedup"]["status_badge"], "No data yet")

    def test_demo_runner_lifecycle_and_alternation(self) -> None:
        """Verify DemoRunner management of paired live A/B runs."""
        from dashboard.demo_runner import DemoRunner
        runner = DemoRunner(db_path=self.db_path, results_dir=Path("benchmarks/results"))

        # Availability check
        avail, reason = runner.check_availability()
        self.assertIsInstance(avail, bool)
        self.assertIsInstance(reason, str)

        # Initial state
        state = runner.get_state()
        self.assertEqual(state["status"], "idle")
        self.assertFalse(state["is_running"])

        # Summary calculation check on synthetic runs
        synthetic_runs = [
            {"pair": 1, "order": "WITHOUT first", "arm": "without", "duration_s": 8.4, "cpu_share": 100},
            {"pair": 1, "order": "WITHOUT first", "arm": "with", "duration_s": 2.1, "cpu_share": 500},
            {"pair": 2, "order": "WITH first", "arm": "with", "duration_s": 2.0, "cpu_share": 500},
            {"pair": 2, "order": "WITH first", "arm": "without", "duration_s": 8.3, "cpu_share": 100},
            {"pair": 3, "order": "WITHOUT first", "arm": "without", "duration_s": 8.5, "cpu_share": 100},
            {"pair": 3, "order": "WITHOUT first", "arm": "with", "duration_s": 2.05, "cpu_share": 500},
            {"pair": 4, "order": "WITH first", "arm": "with", "duration_s": 1.98, "cpu_share": 500},
            {"pair": 4, "order": "WITH first", "arm": "without", "duration_s": 8.2, "cpu_share": 100},
            {"pair": 5, "order": "WITHOUT first", "arm": "without", "duration_s": 8.6, "cpu_share": 100},
            {"pair": 5, "order": "WITHOUT first", "arm": "with", "duration_s": 2.02, "cpu_share": 500},
        ]
        summary = runner._compute_summary(synthetic_runs)
        self.assertEqual(summary["pairs_completed"], 5)
        self.assertGreater(summary["speedup_pct"], 70.0)
        self.assertTrue(summary["is_significant"])
        self.assertIsNotNone(summary["p_value"])
        self.assertLess(summary["p_value"], 0.05)


if __name__ == "__main__":
    unittest.main()
