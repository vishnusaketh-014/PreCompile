"""Lightweight, read-only Python stdlib HTTP and SSE server for PreCompile Dashboard.

Zero build steps, zero CDN dependencies, strictly read-only execution.
Streams real-time updates at 1.0 Hz via Server-Sent Events (SSE).
"""

import json
import logging
import os
import posixpath
import socketserver
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from dashboard.demo_runner import DemoRunner
from dashboard.reader import DashboardDataReader

logger = logging.getLogger("precompile.dashboard.server")
STATIC_DIR = Path(__file__).resolve().parent / "static"
DIST_DIR = STATIC_DIR / "dist"


class DashboardHandler(SimpleHTTPRequestHandler):
    """HTTP request handler supporting static assets, REST API, and SSE streaming."""

    reader: DashboardDataReader
    demo_runner: DemoRunner

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self) -> None:
        """Dispatch route handlers for REST API, SSE, and static single page app."""
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        if path == "/api/compare":
            self._handle_api_compare(query)
        elif path == "/api/overview":
            self._handle_api_overview()
        elif path == "/api/distributions":
            self._handle_api_distributions(query)
        elif path == "/api/timeline":
            self._handle_api_timeline(query)
        elif path == "/api/benefit":
            self._handle_api_benefit(query)
        elif path == "/api/safety":
            self._handle_api_safety()
        elif path == "/api/experiments":
            self._handle_api_experiments()
        elif path == "/api/decisions":
            self._handle_api_decisions(query)
        elif path == "/api/replay/manifest":
            self._handle_api_replay_manifest()
        elif path == "/api/replay/trials":
            self._handle_api_replay_trials(query)
        elif path == "/api/demo/status":
            self._handle_api_demo_status()
        elif path == "/api/stream":
            self._handle_sse_stream()
        else:
            rel = path.lstrip("/")
            if rel in ("", "index.html"):
                index_target = (DIST_DIR / "index.html") if (DIST_DIR / "index.html").is_file() else (STATIC_DIR / "index.html")
                self._send_file(index_target, "text/html; charset=utf-8")
                return

            f = self._find_file(rel)
            if f is not None:
                content_type = self.guess_type(str(f))
                self._send_file(f, content_type)
            elif not path.startswith("/api"):
                # SPA fallback route
                index_target = (DIST_DIR / "index.html") if (DIST_DIR / "index.html").is_file() else (STATIC_DIR / "index.html")
                self._send_file(index_target, "text/html; charset=utf-8")
            else:
                self.send_error(404, "Not found")

    def do_POST(self) -> None:
        """Handle control requests for live A/B demo execution."""
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        if path == "/api/demo/start":
            self._handle_api_demo_start()
        elif path == "/api/demo/stop":
            self._handle_api_demo_stop()
        else:
            self.send_error(404, "Endpoint not found")

    def _handle_api_compare(self, query: Dict[str, list]) -> None:
        """Serve side-by-side empirical comparison for WITH vs WITHOUT PreCompile."""
        scenario = query.get("scenario", ["A"])[0]
        load_level = query.get("load", ["high"])[0]
        source = query.get("source", ["recorded"])[0]
        include_reactive = query.get("reactive", ["1"])[0] in ("1", "true", "True")

        try:
            data = self.reader.get_compare_data(
                scenario=scenario,
                load_level=load_level,
                source=source,
                include_reactive=include_reactive,
            )
            self._send_json(data)
        except Exception as e:
            logger.error("Error in /api/compare: %s", e)
            self._send_json({"error": str(e), "empty": True}, status=500)

    def _handle_api_demo_status(self) -> None:
        """Return status and per-run results from the live A/B demo runner."""
        try:
            data = self.demo_runner.get_state()
            self._send_json(data)
        except Exception as e:
            logger.error("Error in /api/demo/status: %s", e)
            self._send_json({"error": str(e), "status": "error"}, status=500)

    def _handle_api_demo_start(self) -> None:
        """Launch paired live A/B demo."""
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len > 0 else b"{}"
            params = json.loads(body.decode("utf-8")) if body else {}

            pairs = int(params.get("pairs", 3))
            scenario = params.get("scenario", "A")
            load = params.get("load", "high")

            res = self.demo_runner.start(pairs=pairs, scenario=scenario, load_level=load)
            self._send_json(res)
        except Exception as e:
            logger.error("Error starting demo: %s", e)
            self._send_json({"error": str(e), "status": "error"}, status=500)

    def _handle_api_demo_stop(self) -> None:
        """Stop running demo."""
        try:
            res = self.demo_runner.stop()
            self._send_json(res)
        except Exception as e:
            logger.error("Error stopping demo: %s", e)
            self._send_json({"error": str(e)}, status=500)

    def _find_file(self, rel_path: str) -> Optional[Path]:
        clean = rel_path.lstrip("/")
        if DIST_DIR.is_dir():
            dist_file = DIST_DIR / clean
            if dist_file.is_file():
                return dist_file
        static_file = STATIC_DIR / clean
        if static_file.is_file():
            return static_file
        return None

    def _send_file(self, file_path: Path, content_type: str) -> None:
        try:
            content = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            logger.warning("Error serving static file %s: %s", file_path, e)
            self.send_error(500, str(e))

    def _serve_static_file(self, filename: str, content_type: str) -> None:
        """Serve a static file from dist (if available) or static."""
        f = self._find_file(filename) or (STATIC_DIR / filename)
        if not f.exists():
            self.send_error(404, f"File not found: {filename}")
            return
        self._send_file(f, content_type)

    def _handle_api_overview(self) -> None:
        """Serve live system overview and hero metrics."""
        try:
            data = self.reader.get_live_overview()
            self._send_json(data)
        except Exception as e:
            logger.error("Error in /api/overview: %s", e)
            self._send_json({"error": str(e), "empty": True}, status=500)

    def _handle_api_distributions(self, query: Dict[str, list]) -> None:
        """Serve reserved vs holdout build duration and CPU share distributions."""
        scenario = query.get("scenario", [None])[0]
        load_level = query.get("load", [None])[0]
        project_id = query.get("project", [None])[0]

        try:
            data = self.reader.get_reserved_vs_holdout(
                project_id=project_id, scenario=scenario, load_level=load_level
            )
            self._send_json(data)
        except Exception as e:
            logger.error("Error in /api/distributions: %s", e)
            self._send_json({"error": str(e), "empty": True}, status=500)

    def _handle_api_timeline(self, query: Dict[str, list]) -> None:
        """Serve build cycle prediction timeline swimlanes."""
        project_id = query.get("project", [None])[0]
        limit = int(query.get("limit", [30])[0])
        try:
            data = self.reader.get_prediction_timeline(project_id=project_id, limit=limit)
            self._send_json(data)
        except Exception as e:
            logger.error("Error in /api/timeline: %s", e)
            self._send_json({"error": str(e), "empty": True}, status=500)

    def _handle_api_benefit(self, query: Dict[str, list]) -> None:
        """Serve benefit estimation state, bin effects, and weight progression."""
        project_id = query.get("project", [None])[0]
        try:
            data = self.reader.get_benefit_estimator(project_id=project_id)
            self._send_json(data)
        except Exception as e:
            logger.error("Error in /api/benefit: %s", e)
            self._send_json({"error": str(e), "empty": True}, status=500)

    def _handle_api_safety(self) -> None:
        """Serve safety governor status: budget gauge, circuit breaker, leaks, overhead."""
        try:
            data = self.reader.get_safety_governor()
            self._send_json(data)
        except Exception as e:
            logger.error("Error in /api/safety: %s", e)
            self._send_json({"error": str(e), "empty": True}, status=500)

    def _handle_api_experiments(self) -> None:
        """Serve pre-registered benchmark results, criteria status, and ablations."""
        try:
            data = self.reader.get_experiments_evaluation()
            self._send_json(data)
        except Exception as e:
            logger.error("Error in /api/experiments: %s", e)
            self._send_json({"error": str(e), "empty": True}, status=500)

    def _handle_api_decisions(self, query: Dict[str, list]) -> None:
        """Serve filterable stream of daemon arming and abstention decisions."""
        category = query.get("category", [None])[0]
        limit = int(query.get("limit", [50])[0])
        try:
            data = self.reader.get_decision_log(category=category, limit=limit)
            self._send_json(data)
        except Exception as e:
            logger.error("Error in /api/decisions: %s", e)
            self._send_json({"error": str(e), "empty": True}, status=500)

    def _handle_api_replay_manifest(self) -> None:
        """Serve available recorded benchmark suites for interactive playback."""
        try:
            data = self.reader.get_replay_manifest()
            self._send_json(data)
        except Exception as e:
            logger.error("Error in /api/replay/manifest: %s", e)
            self._send_json({"error": str(e), "empty": True}, status=500)

    def _handle_api_replay_trials(self, query: Dict[str, list]) -> None:
        """Serve sequential trial events for replay playback."""
        dataset_id = query.get("dataset", ["results_tier1"])[0]
        try:
            trials = self.reader.get_replay_trials(dataset_id=dataset_id)
            self._send_json({"dataset": dataset_id, "trials": trials})
        except Exception as e:
            logger.error("Error in /api/replay/trials: %s", e)
            self._send_json({"error": str(e), "empty": True}, status=500)

    def _handle_sse_stream(self) -> None:
        """Server-Sent Events endpoint streaming live system state at 1.0 Hz."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        logger.info("SSE client connected to /api/stream")

        try:
            while True:
                # Fetch fresh state snapshot
                overview = self.reader.get_live_overview()
                payload = json.dumps(overview)
                message = f"event: overview\ndata: {payload}\n\n"

                self.wfile.write(message.encode("utf-8"))
                self.wfile.flush()

                # Strictly throttled to 1.0 Hz to prevent host scheduling disturbance
                time.sleep(1.0)
        except (BrokenPipeError, ConnectionResetError):
            logger.info("SSE client disconnected")
        except Exception as e:
            logger.warning("SSE stream terminated: %s", e)

    def _send_json(self, data: Any, status: int = 200) -> None:
        """Serialize and send JSON response."""
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        """Silence standard request logs to keep terminal uncluttered."""
        pass


class ThreadedDashboardServer(socketserver.ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server for handling concurrent API queries and SSE stream."""
    daemon_threads = True
    allow_reuse_address = True


class DashboardServer:
    """Manager for the PreCompile dashboard server lifecycle."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8787,
        db_path: Optional[str] = None,
        results_dir: Optional[str] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.reader = DashboardDataReader(db_path=db_path, results_dir=results_dir)
        self.demo_runner = DemoRunner(db_path=self.reader.db_path, results_dir=self.reader.results_dir)
        self.server: Optional[ThreadedDashboardServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, block: bool = True) -> None:
        """Start the dashboard HTTP server."""
        # Attach reader and demo runner to handler class
        DashboardHandler.reader = self.reader
        DashboardHandler.demo_runner = self.demo_runner
        self.server = ThreadedDashboardServer((self.host, self.port), DashboardHandler)

        print("=" * 68)
        print(f"  PreCompile Mission Control Dashboard")
        print(f"  Listening at: http://localhost:{self.port}")
        print(f"  Access (WSL2/Windows): http://127.0.0.1:{self.port}")
        print(f"  Database Mode: READ-ONLY (file:{self.reader.db_path}?mode=ro)")
        print(f"  Live Polling Rate: 1.0 Hz (minimal CPU overhead)")
        print("=" * 68)

        if block:
            try:
                self.server.serve_forever()
            except KeyboardInterrupt:
                print("\nShutting down PreCompile Dashboard cleanly.")
                self.stop()
        else:
            self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Shutdown the dashboard server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


def run_dashboard(args: Any) -> int:
    """CLI subcommand entry point for `precompile dashboard`."""
    host = getattr(args, "host", "0.0.0.0")
    port = int(getattr(args, "port", 8787))
    db_path = getattr(args, "db", None)
    results_dir = getattr(args, "results_dir", None)

    server = DashboardServer(host=host, port=port, db_path=db_path, results_dir=results_dir)
    server.start(block=True)
    return 0
