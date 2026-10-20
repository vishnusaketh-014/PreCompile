"""Unit tests for Stage 3, 4, 8, 9 DataStore (precompile.store).

Tests project CRUD, delay sample persistence, model state persistence across restarts,
prediction scoring, rolling hit-rate calculation, shadow prediction rules, build records,
WAL/FK pragmas, concurrent multi-threaded inserts, retention pruning, and CSV export.
"""

import csv
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from precompile.config import StoreConfig
from precompile.store import (
    BuildRecord,
    DataStore,
    DelaySampleRecord,
    PredictionRecord,
    ProjectRecord,
)


class TestDataStore(unittest.TestCase):
    """Test suite covering requirements for precompile.store."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(dir="/tmp", prefix="precompile_store_test_")
        self.db_path = Path(self.temp_dir) / "test.db"
        self.store = DataStore(":memory:")

    def tearDown(self) -> None:
        try:
            self.store.close()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_a_project_crud_duplicate_error_and_cascade_delete(self) -> None:
        """Requirement a: project CRUD, duplicate id raises clear error, remove cascades."""
        # 1. Create project
        proj = self.store.add_project(
            project_id="proj_alpha",
            root_path="/tmp/alpha",
            tool_profile_name="gcc_tools",
            enabled=True,
        )
        self.assertEqual(proj.project_id, "proj_alpha")
        self.assertEqual(proj.root_path, "/tmp/alpha")

        # 2. Duplicate id raises a clear ValueError
        with self.assertRaises(ValueError):
            self.store.add_project("proj_alpha", "/tmp/other")

        # 3. Read
        fetched = self.store.get_project("proj_alpha")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.project_id, "proj_alpha")
        self.assertTrue(fetched.enabled)

        # 4. Update enabled
        self.store.set_project_enabled("proj_alpha", False)
        self.assertFalse(self.store.get_project("proj_alpha").enabled)

        # 5. List
        projs = self.store.list_projects()
        self.assertEqual(len(projs), 1)
        self.assertEqual(projs[0].project_id, "proj_alpha")

        # 6. Add dependent sample and prediction
        self.store.record_sample("proj_alpha", "source", "gcc_tools", 1.25)
        pred_id = self.store.record_prediction(
            "proj_alpha", "source", "gcc_tools", time.time(), 1.0, 2.0
        )
        self.assertEqual(self.store.sample_count("proj_alpha", "source", "gcc_tools"), 1)
        self.assertEqual(self.store.prediction_count("proj_alpha", "source", "gcc_tools", scored_only=False), 1)

        # 7. Cascade remove
        removed = self.store.remove_project("proj_alpha")
        self.assertTrue(removed)
        self.assertIsNone(self.store.get_project("proj_alpha"))
        # Cascaded rows must be deleted
        self.assertEqual(self.store.sample_count("proj_alpha", "source", "gcc_tools"), 0)
        self.assertEqual(self.store.prediction_count("proj_alpha", "source", "gcc_tools", scored_only=False), 0)

    def test_b_insert_50_samples_and_query_in_order(self) -> None:
        """Requirement b: insert 50 delay samples and query them back in order; sample_count correct."""
        self.store.add_project("proj_b", "/tmp/b")

        delays = [0.1 * (i + 1) for i in range(50)]
        for d in delays:
            self.store.record_sample("proj_b", "source", "default", d, accepted=True)

        self.assertEqual(self.store.sample_count("proj_b", "source", "default"), 50)

        # Query back in chronological order
        samples = self.store.recent_samples("proj_b", "source", "default", n=50, ascending=True)
        self.assertEqual(len(samples), 50)
        for i, s in enumerate(samples):
            self.assertAlmostEqual(s.delay_s, delays[i], places=4)
            self.assertEqual(s.project_id, "proj_b")
            self.assertEqual(s.file_class, "source")
            self.assertTrue(s.accepted)

    def test_c_model_state_round_trip_and_persistence_across_reopen(self) -> None:
        """Requirement c: model_state save/load round-trips, and persists across closing and reopening a file DB."""
        # 1. Create file-backed DB
        store_file = DataStore(self.db_path)
        store_file.add_project("proj_c", "/tmp/c")

        key = ("proj_c", "source", "gcc")
        store_file.save_model_state(key, n=15, mean=1.42, var=0.08)

        # Verify round-trip in same instance
        state = store_file.load_model_state(key)
        self.assertIsNotNone(state)
        self.assertEqual(state, (15, 1.42, 0.08))
        store_file.close()

        # 2. Reopen existing DB file
        store_reopened = DataStore(self.db_path)
        state_reopened = store_reopened.load_model_state(key)
        self.assertIsNotNone(state_reopened)
        self.assertEqual(state_reopened, (15, 1.42, 0.08))

        # Update model state
        store_reopened.save_model_state(key, n=16, mean=1.45, var=0.075)
        self.assertEqual(store_reopened.load_model_state(key), (16, 1.45, 0.075))
        store_reopened.close()

    def test_d_record_and_score_10_predictions_hit_rate(self) -> None:
        """Requirement d: record and score 10 predictions; recent_hit_rate is correct."""
        self.store.add_project("proj_d", "/tmp/d")

        # 7 hits, 3 misses
        outcomes = ["hit", "hit", "miss", "hit", "hit", "miss", "hit", "hit", "hit", "miss"]
        for out in outcomes:
            pred_id = self.store.record_prediction(
                "proj_d", "source", "default", time.time(), 1.0, 2.0
            )
            self.store.score_prediction(pred_id, out, observed_delay_s=1.5 if out == "hit" else 3.5)

        hits, total, rate = self.store.recent_hit_rate("proj_d", "source", "default", last=10)
        self.assertEqual(total, 10)
        self.assertEqual(hits, 7)
        self.assertAlmostEqual(rate, 0.70, places=4)

    def test_e_only_last_10_count_when_12_scored_and_unscored_excluded(self) -> None:
        """Requirement e: only the last 10 count when 12 are scored; unscored predictions are excluded."""
        self.store.add_project("proj_e", "/tmp/e")

        # Record 2 older predictions (both hits)
        for _ in range(2):
            pid = self.store.record_prediction("proj_e", "source", "default", time.time(), 1.0, 2.0)
            self.store.score_prediction(pid, "hit")

        # Record 10 newer predictions (5 hits, 5 misses)
        for i in range(10):
            pid = self.store.record_prediction("proj_e", "source", "default", time.time(), 1.0, 2.0)
            out = "hit" if i < 5 else "miss"
            self.store.score_prediction(pid, out)

        # Record 3 unscored predictions (in-flight shadow predictions)
        for _ in range(3):
            self.store.record_prediction("proj_e", "source", "default", time.time(), 1.0, 2.0)

        # Query recent_hit_rate over last 10
        hits, total, rate = self.store.recent_hit_rate("proj_e", "source", "default", last=10)
        self.assertEqual(total, 10)
        self.assertEqual(hits, 5)
        self.assertAlmostEqual(rate, 0.50, places=4)

    def test_f_no_build_counts_as_a_miss(self) -> None:
        """Requirement f: no_build counts as a miss."""
        self.store.add_project("proj_f", "/tmp/f")

        p1 = self.store.record_prediction("proj_f", "source", "default", time.time(), 1.0, 2.0)
        p2 = self.store.record_prediction("proj_f", "source", "default", time.time(), 1.0, 2.0)
        p3 = self.store.record_prediction("proj_f", "source", "default", time.time(), 1.0, 2.0)

        self.store.score_prediction(p1, "hit")
        self.store.score_prediction(p2, "no_build")
        self.store.score_prediction(p3, "miss")

        hits, total, rate = self.store.recent_hit_rate("proj_f", "source", "default", last=10)
        self.assertEqual(total, 3)
        self.assertEqual(hits, 1)  # only p1 is a hit
        self.assertAlmostEqual(rate, 1.0 / 3.0, places=4)

    def test_g_fewer_than_5_predictions_rule(self) -> None:
        """Requirement g: fewer than 5 predictions: the API lets the caller tell (prediction_count < 5)."""
        self.store.add_project("proj_g", "/tmp/g")

        for _ in range(4):
            pid = self.store.record_prediction("proj_g", "source", "default", time.time(), 1.0, 2.0)
            self.store.score_prediction(pid, "hit")

        self.assertEqual(self.store.prediction_count("proj_g", "source", "default", scored_only=True), 4)
        self.assertLess(self.store.prediction_count("proj_g", "source", "default", scored_only=True), 5)

        # 5th prediction
        p5 = self.store.record_prediction("proj_g", "source", "default", time.time(), 1.0, 2.0)
        self.store.score_prediction(p5, "hit")
        self.assertEqual(self.store.prediction_count("proj_g", "source", "default", scored_only=True), 5)
        self.assertGreaterEqual(self.store.prediction_count("proj_g", "source", "default", scored_only=True), 5)

    def test_h_builds_recorded_and_fetched_per_condition(self) -> None:
        """Requirement h: builds recorded and fetched per condition."""
        self.store.add_project("proj_h", "/tmp/h")

        t0 = time.time()
        self.store.record_build("proj_h", "reserved", contention_level=12.5, duration_s=4.2, t_start_wall=t0, t_end_wall=t0 + 4.2)
        self.store.record_build("proj_h", "reserved", contention_level=15.0, duration_s=4.0, t_start_wall=t0 + 10, t_end_wall=t0 + 14.0)
        self.store.record_build("proj_h", "holdout", contention_level=12.0, duration_s=6.8, t_start_wall=t0 + 20, t_end_wall=t0 + 26.8)
        self.store.record_build("proj_h", "observed", contention_level=0.5, duration_s=5.1, t_start_wall=t0 + 30, t_end_wall=t0 + 35.1)

        reserved_builds = self.store.builds_by_condition("proj_h", "reserved")
        holdout_builds = self.store.builds_by_condition("proj_h", "holdout")
        observed_builds = self.store.builds_by_condition("proj_h", "observed")

        self.assertEqual(len(reserved_builds), 2)
        self.assertEqual(len(holdout_builds), 1)
        self.assertEqual(len(observed_builds), 1)

        self.assertAlmostEqual(reserved_builds[0].duration_s, 4.2)
        self.assertAlmostEqual(holdout_builds[0].duration_s, 6.8)
        self.assertAlmostEqual(observed_builds[0].duration_s, 5.1)

    def test_i_file_db_wal_fk_and_idempotent_migrate(self) -> None:
        """Requirement i: file DB reports journal_mode == 'wal' and foreign_keys on; reopening runs migrate() safely."""
        with DataStore(self.db_path) as s1:
            cur = s1._conn.execute("PRAGMA journal_mode")
            self.assertEqual(cur.fetchone()[0].lower(), "wal")

            cur = s1._conn.execute("PRAGMA foreign_keys")
            self.assertEqual(cur.fetchone()[0], 1)

            s1.add_project("proj_i", "/tmp/i")
            s1.record_sample("proj_i", "source", "default", 2.0)

            # Explicit re-migration
            s1.migrate()

        # Reopen same file
        with DataStore(self.db_path) as s2:
            self.assertIsNotNone(s2.get_project("proj_i"))
            self.assertEqual(s2.sample_count("proj_i", "source", "default"), 1)
            # Another explicit migration call
            s2.migrate()
            self.assertEqual(s2.sample_count("proj_i", "source", "default"), 1)

    def test_j_concurrent_inserts_from_4_threads(self) -> None:
        """Requirement j: 4 threads each insert 100 samples concurrently: 400 rows, no errors."""
        self.store.add_project("proj_j", "/tmp/j")

        errors: list = []

        def worker(thread_id: int):
            try:
                for i in range(100):
                    self.store.record_sample(
                        project_id="proj_j",
                        file_class="source",
                        tool_profile="default",
                        delay_s=float(thread_id * 100 + i),
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent insert errors: {errors}")
        total = self.store.sample_count("proj_j", "source", "default", accepted_only=False)
        self.assertEqual(total, 400)

    def test_k_prune_keeps_only_newest_n_rows_per_key(self) -> None:
        """Requirement k: prune keeps only the newest N rows per key."""
        self.store.add_project("proj_k", "/tmp/k")

        # Key 1: (proj_k, source, default) -> insert 15 rows
        for i in range(15):
            self.store.record_sample("proj_k", "source", "default", float(i))

        # Key 2: (proj_k, header, default) -> insert 12 rows
        for i in range(12):
            self.store.record_sample("proj_k", "header", "default", float(i))

        # Prune keeping newest 5 per key
        result = self.store.prune(retention_samples=5)
        self.assertEqual(result["pruned_samples"], 17)  # (15 - 5) + (12 - 5) = 17

        # Check remaining
        self.assertEqual(self.store.sample_count("proj_k", "source", "default"), 5)
        self.assertEqual(self.store.sample_count("proj_k", "header", "default"), 5)

        # Verify the 5 remaining are the newest (delays 10..14 for key 1, 7..11 for key 2)
        s1 = self.store.recent_samples("proj_k", "source", "default", n=5, ascending=True)
        self.assertEqual([int(s.delay_s) for s in s1], [10, 11, 12, 13, 14])

    def test_l_export_csv_writes_readable_csv_with_header(self) -> None:
        """Requirement l: export_csv writes a readable CSV with a header row."""
        self.store.add_project("proj_l", "/tmp/l", tool_profile_name="ninja")
        self.store.record_sample("proj_l", "source", "ninja", 1.85)
        self.store.record_sample("proj_l", "source", "ninja", 2.15)

        csv_path = Path(self.temp_dir) / "exports" / "samples.csv"
        rows_exported = self.store.export_csv("delay_samples", csv_path)
        self.assertEqual(rows_exported, 2)
        self.assertTrue(csv_path.exists())

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Header check
        self.assertGreater(len(rows), 1)
        header = rows[0]
        self.assertIn("project_id", header)
        self.assertIn("delay_s", header)
        self.assertIn("tool_profile", header)

        # Data check
        self.assertEqual(rows[1][header.index("project_id")], "proj_l")
        self.assertEqual(rows[1][header.index("tool_profile")], "ninja")


if __name__ == "__main__":
    unittest.main()
