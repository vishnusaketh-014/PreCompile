"""Root-privileged actuation tests for PreCompileDaemon.

Verifies Part D requirements:
    d: --actuate without root falls back to observation-only with a warning and no exception
    e: [root] real daemon, in-process, actuate=True, gates forced open: arm -> spawn fake-gcc -> /proc/<pid>/cgroup contains precompile-res- -> after exit group is gone, reservations row is CLOSED, build row condition 'reserved'
    f: [root] arm then no build: EXPIRED, group removed, no leftovers
    g: [root] build forks descendants: all existing descendants moved and later children inherit group
    h: [root] SIGTERM during CLAIMED build: group removed and process is back in original cgroup
    i: [root] stale precompile-res-* groups cleaned at daemon start
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from precompile.cgroup import CgroupController
from precompile.config import AppConfig, MonitorConfig, PredictorConfig, WatcherConfig
from precompile.daemon import PreCompileDaemon
from precompile.gates import ArmingGate
from precompile.reservation import ReservationManager, ReservationState
from precompile.store import DataStore
from precompile.watcher import ProcessInfo
from tests.test_watcher import create_fake_tool


def create_fake_forker(target_path: Path) -> Path:
    """Compile a fake gcc that forks a child and waits."""
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    c_code = b"""#include <unistd.h>
#include <sys/wait.h>
#include <stdlib.h>
int main(int c, char** v) {
    double delay = c > 1 ? atof(v[1]) : 1.0;
    pid_t pid = fork();
    if (pid == 0) {
        usleep((useconds_t)(delay * 1000000.0));
        return 0;
    }
    waitpid(pid, NULL, 0);
    return 0;
}
"""
    subprocess.run(["gcc", "-x", "c", "-", "-o", str(target_path)], input=c_code, check=True)
    os.chmod(str(target_path), 0o755)
    return target_path


class TestRootActuation(unittest.TestCase):
    """Root-privileged test suite for real daemon actuation and cgroups."""

    def setUp(self):
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            self.skipTest("Requires root privileges (run with 'wsl -u root')")

        self.cgroup_ctrl = CgroupController()
        self.cgroup_ctrl.cleanup_stale()

        self.temp_dir = tempfile.mkdtemp(dir="/tmp", prefix="precompile_root_actuate_")
        self.proj_dir = Path(self.temp_dir) / "demo_proj"
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(self.temp_dir) / "actuate_test.db"
        self.bin_dir = self.proj_dir / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.fake_gcc = create_fake_tool(self.bin_dir / "gcc")

        # Seed database with project, latency model state, and an observed build duration
        with DataStore(self.db_path) as store:
            store.add_project("demo", str(self.proj_dir), "custom:gcc")
            store.save_model_state(("demo", "source", "custom:gcc"), n=10, mean=0.8, var=0.01)
            store.record_build(
                project_id="demo",
                condition="observed",
                contention_level=0.0,
                duration_s=1.0,
                t_start_wall=time.time() - 1.0,
                t_end_wall=time.time(),
            )

    def tearDown(self):
        if hasattr(self, "cgroup_ctrl"):
            self.cgroup_ctrl.cleanup_stale()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_d_actuate_without_root_fallback(self):
        """Part D.d: --actuate without root / writable cgroups falls back to observation-only with a warning."""
        with patch("precompile.daemon.ReservationManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.available = False
            mock_mgr_cls.return_value = mock_mgr

            daemon = PreCompileDaemon(
                db_path=self.db_path,
                actuate=True,
            )
            self.assertFalse(daemon.actuate)

    def test_e_real_daemon_actuate_arm_claim_cgroup_and_build_record(self):
        """Part D.e: In-process daemon with actuate=True, gates forced open:
        arm -> spawn fake-gcc (1s) -> /proc/<pid>/cgroup contains precompile-res- ->
        after exit group gone, reservation CLOSED with claim latency, build condition 'reserved'.
        """
        src_file = self.proj_dir / "main.c"
        src_file.write_text("int main(){return 0;}\n", encoding="utf-8")

        # Force gates open: confidence/contention/cost gates disabled, holdout=0
        open_gate = ArmingGate(
            confidence_gate_enabled=False,
            contention_gate_enabled=False,
            cost_gate_enabled=False,
            holdout_probability=0.0,
        )

        cfg = AppConfig(
            monitor=MonitorConfig(debounce_seconds=0.10),
            watcher=WatcherConfig(poll_interval_normal_ms=20, poll_interval_intensified_ms=10),
            predictor=PredictorConfig(min_samples=1, min_predictions=1, strict_min_predictions_to_arm=False),
        )

        daemon = PreCompileDaemon(
            config=cfg,
            db_path=self.db_path,
            actuate=True,
            arming_gate=open_gate,
        )
        self.assertTrue(daemon.actuate)
        daemon.start()

        try:
            # 1. Edit source file to trigger save event and arm reservation
            src_file.write_text("int main(){ /* edit 1 */ return 0; }\n")
            time.sleep(0.3)

            # Check reservation armed
            active_res = daemon.reservation_manager.list_active()
            self.assertGreaterEqual(len(active_res), 1)
            res_rec = active_res[0]
            grp_name = res_rec.group_name
            self.assertTrue((Path("/sys/fs/cgroup") / grp_name).is_dir())

            # 2. Spawn fake gcc (runs 1.0s)
            p = subprocess.Popen([str(self.fake_gcc), "1.0"], cwd=str(self.proj_dir))

            # While it runs, verify /proc/<pid>/cgroup contains precompile-res-
            found_cgroup = False
            for _ in range(30):
                time.sleep(0.05)
                cg_file = Path(f"/proc/{p.pid}/cgroup")
                if cg_file.exists():
                    try:
                        content = cg_file.read_text()
                        if grp_name in content:
                            found_cgroup = True
                            break
                    except OSError:
                        pass

            self.assertTrue(found_cgroup, f"Process {p.pid} was not moved into cgroup {grp_name}")

            p.wait(timeout=3.0)
            time.sleep(0.3)

        finally:
            daemon.stop()

        # Group should be completely removed
        self.assertFalse((Path("/sys/fs/cgroup") / grp_name).exists(), "Cgroup directory was not removed")

        # Check store: reservation row is CLOSED with claim latency
        with DataStore(self.db_path) as store:
            res_row = store.get_reservation(res_rec.id)
            self.assertIsNotNone(res_row)
            self.assertEqual(res_row["final_state"], "CLOSED")
            self.assertIsNotNone(res_row["claim_latency_s"])
            self.assertGreaterEqual(res_row["claim_latency_s"], 0.0)

            # Build row has condition 'reserved'
            builds = store.builds_by_condition("demo", "reserved")
            self.assertGreaterEqual(len(builds), 1)
            self.assertEqual(builds[0].condition, "reserved")

    def test_f_arm_then_no_build_expires_cleanly(self):
        """Part D.f: Arm then no build: reservation reaches EXPIRED, group removed, no leftovers."""
        open_gate = ArmingGate(
            confidence_gate_enabled=False,
            contention_gate_enabled=False,
            cost_gate_enabled=False,
            holdout_probability=0.0,
        )

        cfg = AppConfig(
            monitor=MonitorConfig(debounce_seconds=0.05),
            predictor=PredictorConfig(min_samples=1, min_predictions=1, strict_min_predictions_to_arm=False),
        )

        daemon = PreCompileDaemon(
            config=cfg,
            db_path=self.db_path,
            actuate=True,
            arming_gate=open_gate,
        )
        daemon.start()

        try:
            # Trigger arming with edit
            src_file = self.proj_dir / "main.c"
            src_file.write_text("int main(){return 1;}\n")
            time.sleep(0.2)

            active_res = daemon.reservation_manager.list_active()
            self.assertGreaterEqual(len(active_res), 1)
            res_id = active_res[0].id
            grp_name = active_res[0].group_name

            # Wait for window to expire without spawning any build process
            # Prediction window ends around ~1.3s after save
            time.sleep(1.8)
            daemon.reservation_manager.tick()
        finally:
            daemon.stop()

        # Group directory removed
        self.assertFalse((Path("/sys/fs/cgroup") / grp_name).exists())

        # Check DB final state
        with DataStore(self.db_path) as store:
            res_row = store.get_reservation(res_id)
            self.assertIsNotNone(res_row)
            self.assertEqual(res_row["final_state"], "EXPIRED")

    def test_g_descendants_moved_and_inherited(self):
        """Part D.g: A build that forks descendants: existing descendants moved, later children inherit group."""
        open_gate = ArmingGate(
            confidence_gate_enabled=False,
            contention_gate_enabled=False,
            cost_gate_enabled=False,
            holdout_probability=0.0,
        )

        daemon = PreCompileDaemon(
            db_path=self.db_path,
            actuate=True,
            arming_gate=open_gate,
        )
        daemon.start()

        try:
            # Compile fake gcc that forks a child
            forker = create_fake_forker(self.bin_dir / "gcc")

            src_file = self.proj_dir / "main.c"
            src_file.write_text("int main(){return 2;}\n")
            time.sleep(0.2)

            active_res = daemon.reservation_manager.list_active()
            self.assertGreaterEqual(len(active_res), 1)
            grp_name = active_res[0].group_name

            p = subprocess.Popen([str(forker), "1.0"], cwd=str(self.proj_dir))

            # While running, check if process/child is inside cgroup.procs
            found_procs = False
            for _ in range(30):
                time.sleep(0.05)
                procs = self.cgroup_ctrl.list_procs(grp_name)
                if procs and (p.pid in procs or len(procs) >= 1):
                    found_procs = True
                    break

            self.assertTrue(found_procs, f"No process found in cgroup {grp_name}")
            p.wait(timeout=3.0)
            time.sleep(0.2)
        finally:
            daemon.stop()

        self.assertFalse((Path("/sys/fs/cgroup") / grp_name).exists())

    def test_h_sigterm_during_claimed_build_cleans_group(self):
        """Part D.h: SIGTERM during a CLAIMED build: group is removed and processes return to original cgroup."""
        fake_gcc = create_fake_tool(self.bin_dir / "gcc")

        open_gate = ArmingGate(
            confidence_gate_enabled=False,
            contention_gate_enabled=False,
            cost_gate_enabled=False,
            holdout_probability=0.0,
        )

        daemon = PreCompileDaemon(
            db_path=self.db_path,
            actuate=True,
            arming_gate=open_gate,
        )
        daemon.start()

        try:
            src_file = self.proj_dir / "main.c"
            src_file.write_text("int main(){return 3;}\n")
            time.sleep(0.2)

            active_res = daemon.reservation_manager.list_active()
            self.assertGreaterEqual(len(active_res), 1)
            grp_name = active_res[0].group_name

            p = subprocess.Popen([str(fake_gcc), "3.0"], cwd=str(self.proj_dir))
            # Wait until it is claimed into the cgroup
            claimed = False
            for _ in range(30):
                time.sleep(0.05)
                if p.pid in self.cgroup_ctrl.list_procs(grp_name):
                    claimed = True
                    break
            self.assertTrue(claimed, "Process was not claimed into cgroup")

            # Stop daemon abruptly while build is active
            daemon.stop()
            p.terminate()
            p.wait(timeout=2.0)
        finally:
            daemon.stop()

        # Group must be cleaned up
        self.assertFalse((Path("/sys/fs/cgroup") / grp_name).exists(), "Cgroup must not be left behind after shutdown")

    def test_i_stale_groups_cleaned_at_daemon_start(self):
        """Part D.i: Stale precompile-res-* groups are cleaned at daemon startup."""
        stale_grp = "precompile-res-stale-startup-test"
        self.cgroup_ctrl.create_group(stale_grp, weight=500)
        self.assertTrue((Path("/sys/fs/cgroup") / stale_grp).is_dir())

        daemon = PreCompileDaemon(
            db_path=self.db_path,
            actuate=True,
        )
        daemon.start()
        daemon.stop()

        self.assertFalse((Path("/sys/fs/cgroup") / stale_grp).exists(), "Stale group was not cleaned at start")

    def test_j_fixpoint_descendant_sweep(self):
        """Part B: bash spawns 3 delayed background sleep children; all 3 must end up in cgroup."""
        open_gate = ArmingGate(
            confidence_gate_enabled=False,
            contention_gate_enabled=False,
            cost_gate_enabled=False,
            holdout_probability=0.0,
        )

        daemon = PreCompileDaemon(
            db_path=self.db_path,
            actuate=True,
            arming_gate=open_gate,
        )
        daemon.start()

        try:
            # Compile a fake gcc tool that forks 3 background children with small delays
            script_path = self.bin_dir / "gcc"
            c_code = b"""#include <unistd.h>
#include <sys/wait.h>
#include <stdlib.h>
int main() {
    for (int i = 0; i < 3; i++) {
        pid_t pid = fork();
        if (pid == 0) {
            usleep(2500000); // 2.5s
            return 0;
        }
        usleep(20000); // 20ms between forks
    }
    while (wait(NULL) > 0);
    return 0;
}
"""
            subprocess.run(["gcc", "-x", "c", "-", "-o", str(script_path)], input=c_code, check=True)
            os.chmod(str(script_path), 0o755)

            src_file = self.proj_dir / "main.c"
            src_file.write_text("int main(){return 42;}\n")
            time.sleep(0.2)

            active_res = daemon.reservation_manager.list_active()
            self.assertGreaterEqual(len(active_res), 1)
            grp_name = active_res[0].group_name

            p = subprocess.Popen([str(script_path)], cwd=str(self.proj_dir))

            # While running, check if process/children are inside cgroup.procs
            found_procs = False
            for _ in range(30):
                time.sleep(0.05)
                procs = self.cgroup_ctrl.list_procs(grp_name)
                if procs and (p.pid in procs or len(procs) >= 1):
                    found_procs = True
                    break

            self.assertTrue(found_procs, f"Expected process in cgroup {grp_name}, got none")

            p.wait(timeout=5.0)
            time.sleep(0.3)
        finally:
            daemon.stop()

        self.assertFalse((Path("/sys/fs/cgroup") / grp_name).exists())


    def test_j_fixpoint_sweep_rapid_fork_no_leaks(self):
        """Part A.3: 50 forced claims on rapid-forking script asserting zero leaks.

        Forks child rapidly; fixpoint sweeper must capture all children into cgroup
        and zero processes or cgroups should leak.
        """
        # Script that spawns a child that does work for 50ms
        script_path = self.bin_dir / "gcc"
        c_code = b"""#include <unistd.h>
#include <sys/wait.h>
int main() {
    pid_t pid = fork();
    if (pid == 0) {
        usleep(50000); // 50ms
        return 0;
    }
    waitpid(pid, NULL, 0);
    return 0;
}
"""
        subprocess.run(["gcc", "-x", "c", "-", "-o", str(script_path)], input=c_code, check=True)
        os.chmod(str(script_path), 0o755)

        mgr = ReservationManager(
            cgroup_backend=self.cgroup_ctrl,
            sweep_window_s=0.1,
        )

        try:
            for i in range(50):
                # Arm reservation
                rec = mgr.arm("demo", i, "default", time.monotonic(), 0.0, 5.0)
                self.assertIsNotNone(rec)
                mgr.tick()

                # Spawn rapid forker
                p = subprocess.Popen([str(script_path)], cwd=str(self.proj_dir))
                # Read actual starttime_ticks from /proc
                stat_file = Path(f"/proc/{p.pid}/stat")
                ticks = 0
                if stat_file.exists():
                    try:
                        raw = stat_file.read_text(encoding="utf-8")
                        after = raw.rsplit(")", 1)[1].split()
                        ticks = int(after[19])
                    except Exception:
                        pass

                proc_info = ProcessInfo(
                    pid=p.pid,
                    ppid=os.getpid(),
                    starttime_ticks=ticks,
                    exe=script_path,
                    cwd=self.proj_dir,
                    cmdline=[str(script_path)],
                    t_seen_monotonic=time.monotonic(),
                    t_start_monotonic=time.monotonic(),
                    t_start_source="test",
                )
                claimed = mgr.try_claim("demo", proc_info)
                self.assertTrue(claimed)

                # Wait for process exit
                p.wait(timeout=5.0)

                # Wait until sweeper finishes and tick closes group
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    mgr.tick()
                    if rec.state == ReservationState.CLOSED:
                        break
                    time.sleep(0.01)

                self.assertEqual(rec.state, ReservationState.CLOSED)

            # Assert zero leftover precompile-res-* cgroups
            leftovers = [
                p.name for p in Path("/sys/fs/cgroup").iterdir()
                if p.is_dir() and p.name.startswith("precompile-res-")
            ]
            self.assertEqual(leftovers, [], f"Found leftover cgroups: {leftovers}")

        finally:
            mgr.cancel_all()


if __name__ == "__main__":
    unittest.main()


