"""Tests for PreCompile Dashboard server and static asset delivery."""

import json
import unittest
from pathlib import Path

from dashboard.reader import DashboardDataReader
from dashboard.server import DashboardServer, STATIC_DIR


class TestDashboardServer(unittest.TestCase):
    """Test suite covering dashboard server configuration and static assets."""

    def test_static_assets_exist(self) -> None:
        """Verify all required SPA assets exist locally with no external CDN dependencies."""
        index_html = STATIC_DIR / "index.html"
        style_css = STATIC_DIR / "style.css"
        app_js = STATIC_DIR / "app.js"
        charts_js = STATIC_DIR / "charts.js"

        self.assertTrue(index_html.exists(), "index.html missing")
        self.assertTrue(style_css.exists(), "style.css missing")
        self.assertTrue(app_js.exists(), "app.js missing")
        self.assertTrue(charts_js.exists(), "charts.js missing")

        # Verify offline integrity: NO external http/https CDN references in index.html
        html_content = index_html.read_text(encoding="utf-8")
        self.assertNotIn("http://", html_content, "index.html contains external unencrypted URLs")
        self.assertNotIn("https://", html_content, "index.html contains external CDN references")

    def test_server_initialization_and_reader(self) -> None:
        """Verify DashboardServer correctly initializes reader with read-only semantics."""
        server = DashboardServer(host="127.0.0.1", port=8787)
        self.assertEqual(server.host, "127.0.0.1")
        self.assertEqual(server.port, 8787)
        self.assertIsInstance(server.reader, DashboardDataReader)

    def test_api_overview_payload_structure(self) -> None:
        """Verify /api/overview returns valid JSON matching the schema requirements."""
        reader = DashboardDataReader()
        overview = reader.get_live_overview()

        # Validate schema
        self.assertIn("state_machine", overview)
        self.assertIn("current_state", overview["state_machine"])
        self.assertIn("states", overview["state_machine"])
        self.assertIn("tiles", overview)

        # Validate 5 hero tiles exist
        tiles = overview["tiles"]
        self.assertIn("confidence", tiles)
        self.assertIn("delay", tiles)
        self.assertIn("weight", tiles)
        self.assertIn("psi_pressure", tiles)
        self.assertIn("safety", tiles)

        # Validate transitions array
        self.assertIsInstance(overview["transitions"], list)

    def test_api_distributions_payload_structure(self) -> None:
        """Verify /api/distributions returns valid JSON with summary, reserved, and holdout stats."""
        reader = DashboardDataReader()
        dist = reader.get_reserved_vs_holdout()

        self.assertIn("source", dist)
        self.assertIn("summary", dist)
        self.assertIn("reserved", dist)
        self.assertIn("holdout", dist)
        self.assertIn("contention_bins", dist)
        self.assertIn("cpu_share", dist)

    def test_all_api_endpoint_payloads(self) -> None:
        """Verify payload schemas for all 7 view REST endpoints."""
        reader = DashboardDataReader()

        # Timeline
        timeline = reader.get_prediction_timeline()
        self.assertIn("cycles", timeline)
        self.assertIn("total_cycles", timeline)

        # Benefit
        benefit = reader.get_benefit_estimator()
        self.assertIn("state", benefit)
        self.assertIn("evaluations", benefit)
        self.assertIn("weight_history", benefit)

        # Safety
        safety = reader.get_safety_governor()
        self.assertIn("budget", safety)
        self.assertIn("circuit_breaker", safety)
        self.assertIn("kill_switch", safety)
        self.assertIn("daemon_overhead", safety)

        # Experiments
        experiments = reader.get_experiments_evaluation()
        self.assertIn("criteria_status", experiments)
        self.assertIn("conditions_matrix", experiments)
        self.assertIn("ablations", experiments)

        # Decisions
        decisions = reader.get_decision_log()
        self.assertIn("decisions", decisions)
        self.assertIn("count", decisions)

        # Replay Manifest
        manifest = reader.get_replay_manifest()
        self.assertIn("datasets", manifest)

        # Compare Hero Endpoint
        compare = reader.get_compare_data(scenario="A", load_level="high")
        self.assertIn("without", compare)
        self.assertIn("with_precompile", compare)
        self.assertIn("reactive_only", compare)
        self.assertIn("speedup", compare)
        self.assertIn("overlay_timeline", compare)

    def test_demo_runner_integration(self) -> None:
        """Verify server demo_runner instance availability and state schema."""
        server = DashboardServer(host="127.0.0.1", port=8788)
        self.assertIsNotNone(server.demo_runner)

        state = server.demo_runner.get_state()
        self.assertIn("status", state)
        self.assertIn("available", state)
        self.assertIn("runs", state)
        self.assertIn("summary", state)


if __name__ == "__main__":
    unittest.main()
