"""Tests for Linux cgroup v2 controller (precompile/cgroup.py).

Tests unit functionality with FakeCgroupBackend (any user),
and live kernel cgroup tests when running as root (wsl -u root).
"""

import os
import subprocess
import time
import unittest
from pathlib import Path

from precompile.cgroup import CgroupController, CgroupError, FakeCgroupBackend


class TestCgroupUnit(unittest.TestCase):
    """Unit tests for cgroup controller interfaces and FakeCgroupBackend."""

    def setUp(self):
        self.backend = FakeCgroupBackend(weight_cap=500, default_weight=500)

    def test_weight_clamping(self):
        """Verify weights are clamped to [1, weight_cap]."""
        ctrl = CgroupController(weight_cap=500, default_weight=500)
        self.assertEqual(ctrl._clamp_weight(1000), 500)
        self.assertEqual(ctrl._clamp_weight(0), 1)
        self.assertEqual(ctrl._clamp_weight(-10), 1)
        self.assertEqual(ctrl._clamp_weight(250), 250)
        self.assertEqual(ctrl._clamp_weight(None), 500)

    def test_fake_backend_lifecycle(self):
        """Verify FakeCgroupBackend implements full lifecycle."""
        self.backend.ensure_cpu_controller()
        self.assertTrue(self.backend.cpu_controller_enabled)

        # Create
        p = self.backend.create_group("precompile-res-123", weight=600)
        self.assertEqual(self.backend.groups["precompile-res-123"]["weight"], 500)
        self.assertFalse(self.backend.is_populated("precompile-res-123"))

        # Move PID
        self.backend.move_pid("precompile-res-123", 9999)
        self.assertTrue(self.backend.is_populated("precompile-res-123"))
        self.assertEqual(self.backend.list_procs("precompile-res-123"), [9999])
        self.assertEqual(self.backend.original_cgroup_of(9999), "/precompile-res-123")

        # Set weight
        self.backend.set_weight("precompile-res-123", 100)
        self.assertEqual(self.backend.groups["precompile-res-123"]["weight"], 100)

        # Stale check
        self.assertEqual(self.backend.list_stale_groups(), ["precompile-res-123"])

        # Release
        self.backend.release_group("precompile-res-123", fallback_target="/init.scope")
        self.assertNotIn("precompile-res-123", self.backend.groups)
        self.assertEqual(self.backend.original_cgroup_of(9999), "/init.scope")

    def test_stale_cleanup(self):
        """Verify cleanup_stale releases all groups with prefix."""
        self.backend.create_group("precompile-res-a")
        self.backend.create_group("precompile-res-b")
        self.backend.create_group("other-cgroup")
        self.assertEqual(len(self.backend.list_stale_groups()), 2)

        cleaned = self.backend.cleanup_stale()
        self.assertEqual(cleaned, 2)
        self.assertIn("other-cgroup", self.backend.groups)
        self.assertEqual(len(self.backend.list_stale_groups()), 0)


class TestCgroupLiveRoot(unittest.TestCase):
    """Root-only live kernel cgroup v2 tests."""

    def setUp(self):
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            self.skipTest("Requires root privileges (run with 'wsl -u root')")

        self.ctrl = CgroupController(base_cgroup_path="/sys/fs/cgroup")
        # Ensure cpu controller enabled
        self.ctrl.ensure_cpu_controller()

    def test_create_move_populated_and_release(self):
        """Root test: create real group, write weight, move sleeping process, verify /proc/<pid>/cgroup, populated flip, release."""
        grp_name = f"precompile-res-test-{int(time.time())}"
        try:
            # 1. Create real group with weight 500
            grp_path = self.ctrl.create_group(grp_name, weight=500)
            self.assertTrue(grp_path.exists())

            weight_val = (grp_path / "cpu.weight").read_text().strip()
            self.assertEqual(weight_val, "500")

            # 2. Start a sleeping process
            p = subprocess.Popen(["sleep", "3"])
            orig_cg = self.ctrl.original_cgroup_of(p.pid)
            self.assertIsNotNone(orig_cg)

            # Move to reservation cgroup
            self.ctrl.move_pid(grp_name, p.pid)
            new_cg = self.ctrl.original_cgroup_of(p.pid)
            self.assertTrue(new_cg.endswith(grp_name), f"Expected cgroup ending with {grp_name}, got {new_cg}")

            # Verify populated is True
            self.assertTrue(self.ctrl.is_populated(grp_name))
            self.assertIn(p.pid, self.ctrl.list_procs(grp_name))

            # 3. Kill process and verify populated flips to False
            p.kill()
            p.wait(timeout=5)
            # Give kernel brief moment to update cgroup.events
            time.sleep(0.1)
            self.assertFalse(self.ctrl.is_populated(grp_name))

            # 4. Remove group
            self.ctrl.remove_group(grp_name)
            self.assertFalse(grp_path.exists())
        finally:
            self.ctrl.cleanup_stale(prefix="precompile-res-test-")

    def test_release_group_moves_live_process_back(self):
        """Root test: release_group moves live process back to root or fallback target."""
        grp_name = f"precompile-res-test-live-{int(time.time())}"
        p = subprocess.Popen(["sleep", "5"])
        try:
            self.ctrl.create_group(grp_name, weight=300)
            self.ctrl.move_pid(grp_name, p.pid)
            self.assertTrue(self.ctrl.original_cgroup_of(p.pid).endswith(grp_name))

            # Release group while process is still alive
            self.ctrl.release_group(grp_name, fallback_target="/")
            # Group should be removed
            self.assertFalse((Path("/sys/fs/cgroup") / grp_name).exists())

            # Process should be back in root or fallback
            after_cg = self.ctrl.original_cgroup_of(p.pid)
            self.assertFalse(after_cg.endswith(grp_name))
        finally:
            p.kill()
            p.wait(timeout=5)
            self.ctrl.cleanup_stale(prefix="precompile-res-test-")


if __name__ == "__main__":
    unittest.main()
