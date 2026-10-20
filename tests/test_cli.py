"""Unit and subprocess tests for PreCompile CLI (precompile/cli.py).

Verifies Requirements a, b, j from Step 6 specifications:
    a. CLI projects add/list/remove/enable/disable against a temporary DB, with /mnt/ refusal.
    b. status and predict --json output parse correctly on a seeded DB.
    j. Subprocess test: `python3 -m precompile daemon` runs as a real subprocess, responds to
       SIGTERM, exits with code 0 within 3 seconds, and removes its PID file.
"""

import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from precompile.cli import build_parser, main
from precompile.store import DataStore


class TestCli(unittest.TestCase):
    """Test suite for CLI subcommands."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(dir="/tmp", prefix="precompile_cli_test_")
        self.db_path = Path(self.temp_dir) / "test_cli.db"
        self.project_path = Path(self.temp_dir) / "my_project"
        self.project_path.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_a_projects_crud_and_mnt_rejection(self) -> None:
        """Requirement a: projects add/list/remove/enable/disable and /mnt/ refusal."""
        # 1. Add project
        rc = main([
            "projects", "add",
            "--id", "proj_a",
            "--path", str(self.project_path),
            "--tools", "custom:gcc,make",
            "--db", str(self.db_path),
        ])
        self.assertEqual(rc, 0)

        # Verify added in store
        with DataStore(self.db_path) as store:
            p = store.get_project("proj_a")
            self.assertIsNotNone(p)
            self.assertEqual(p.root_path, str(self.project_path))
            self.assertEqual(p.tool_profile_name, "custom:gcc,make")
            self.assertTrue(p.enabled)

        # 2. List projects
        f = io.StringIO()
        with redirect_stdout(f):
            rc = main(["projects", "list", "--db", str(self.db_path)])
        self.assertEqual(rc, 0)
        output = f.getvalue()
        self.assertIn("proj_a", output)
        self.assertIn("custom:gcc,make", output)

        # 3. Disable project
        rc = main(["projects", "disable", "--id", "proj_a", "--db", str(self.db_path)])
        self.assertEqual(rc, 0)
        with DataStore(self.db_path) as store:
            p = store.get_project("proj_a")
            self.assertFalse(p.enabled)

        # 4. Enable project
        rc = main(["projects", "enable", "--id", "proj_a", "--db", str(self.db_path)])
        self.assertEqual(rc, 0)
        with DataStore(self.db_path) as store:
            p = store.get_project("proj_a")
            self.assertTrue(p.enabled)

        # 5. Remove project
        rc = main(["projects", "remove", "--id", "proj_a", "--db", str(self.db_path)])
        self.assertEqual(rc, 0)
        with DataStore(self.db_path) as store:
            p = store.get_project("proj_a")
            self.assertIsNone(p)

        # 6. Refuse /mnt/ path
        f_mnt = io.StringIO()
        with redirect_stdout(f_mnt):
            rc_mnt = main([
                "projects", "add",
                "--id", "bad_proj",
                "--path", "/mnt/c/Users/someone/project",
                "--db", str(self.db_path),
            ])
        self.assertEqual(rc_mnt, 1)
        self.assertIn("Refusing project path", f_mnt.getvalue())

    def test_b_status_and_predict_json(self) -> None:
        """Requirement b: status and predict --json output parse correctly on a seeded DB."""
        # Seed DB
        with DataStore(self.db_path) as store:
            store.add_project("proj_b", str(self.project_path), "default")
            # Seed 5 delay samples
            for d in [1.80, 1.82, 1.78, 1.81, 1.79]:
                store.record_sample("proj_b", "source", "default", delay_s=d, accepted=True)
            store.save_model_state(("proj_b", "source", "default"), n=5, mean=1.80, var=0.0002)

            # Record a build
            store.record_build("proj_b", "observed", contention_level=1.5, duration_s=0.45, t_start_wall=100.0, t_end_wall=100.45)

        # Test 'status --json'
        f_status = io.StringIO()
        with redirect_stdout(f_status):
            rc = main(["status", "--json", "--db", str(self.db_path)])
        self.assertEqual(rc, 0)
        status_json = json.loads(f_status.getvalue())
        self.assertTrue(status_json["mode"].startswith("observation-only"))
        self.assertEqual(status_json["projects_count"], 1)
        proj_data = status_json["projects"][0]
        self.assertEqual(proj_data["id"], "proj_b")
        self.assertEqual(proj_data["observed_builds"], 1)
        self.assertIn("source", proj_data["models"])
        self.assertEqual(proj_data["models"]["source"]["n"], 5)
        self.assertAlmostEqual(proj_data["models"]["source"]["mean"], 1.80, places=2)

        # Test 'predict --json'
        f_predict = io.StringIO()
        with redirect_stdout(f_predict):
            rc = main(["predict", "--project", "proj_b", "--file-class", "source", "--json", "--db", str(self.db_path)])
        self.assertEqual(rc, 0)
        predict_json = json.loads(f_predict.getvalue())
        self.assertEqual(predict_json["project_id"], "proj_b")
        self.assertEqual(predict_json["file_class"], "source")
        self.assertEqual(predict_json["n"], 5)
        self.assertTrue(predict_json["gate_result"]["can_predict"])

    def test_j_daemon_subprocess_sigterm_clean_exit(self) -> None:
        """Requirement j: Run `python3 -m precompile daemon` as subprocess, send SIGTERM, exit 0 in <=3s."""
        pid_file = Path(self.temp_dir) / "daemon.pid"

        cmd = [
            sys.executable,
            "-m",
            "precompile",
            "daemon",
            "--db", str(self.db_path),
            "--pid-file", str(pid_file),
        ]

        env = os.environ.copy()
        env["PYTHONPATH"] = "."

        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for pid file to appear (up to 3s)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if pid_file.exists():
                break
            time.sleep(0.05)

        self.assertTrue(pid_file.exists(), "Daemon PID file was not created within 3s")

        # Send SIGTERM
        proc.send_signal(signal.SIGTERM)

        # Wait for clean termination within 3s
        try:
            return_code = proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail("Daemon did not terminate within 3s after SIGTERM")
        finally:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

        self.assertEqual(return_code, 0, "Daemon did not exit with code 0 on SIGTERM")
        self.assertFalse(pid_file.exists(), "Daemon did not remove its PID file upon shutdown")


if __name__ == "__main__":
    unittest.main()
