"""Unit tests for Stage 2 & 7 Identity Matcher (precompile.matcher).

Tests identity matching rules, prefix/version regex matching, path containment,
cgroup exclusion, topmost ancestor resolution, subtree descendant querying,
and an end-to-end live spawn identification test.
"""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import Dict, List

from precompile.matcher import (
    DEFAULT_TOOL_PROFILE,
    IdentityMatcher,
    ToolProfile,
    get_descendants,
    is_path_inside,
    is_tool_match,
    match,
    resolve_topmost_ancestor,
)
from precompile.watcher import ProcessInfo, ProcessWatcher
from tests.test_watcher import create_fake_tool


class TestMatcher(unittest.TestCase):
    """Test suite covering requirements for precompile.matcher."""

    def setUp(self) -> None:
        self.temp_base = tempfile.mkdtemp(dir="/tmp", prefix="precompile_match_test_")
        self.proj_dir = Path(self.temp_base) / "my_project"
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.tool_profile = ToolProfile(name="test_tools", binaries={"gcc", "g++", "fake-gcc"})

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_base, ignore_errors=True)

    def test_e_match_succeeds_right_tool_and_cwd_inside_project(self) -> None:
        """Requirement e: match succeeds when tool is in profile and cwd is inside project."""
        proc = ProcessInfo(
            pid=123,
            ppid=1,
            starttime_ticks=1000,
            exe=Path("/usr/bin/gcc"),
            cwd=self.proj_dir / "src",
            cmdline=["gcc", "-c", "main.c"],
            t_seen_monotonic=10.0,
            t_start_monotonic=9.9,
        )
        (self.proj_dir / "src").mkdir(parents=True, exist_ok=True)

        matched = match(
            process=proc,
            project_root=self.proj_dir,
            tool_profile=self.tool_profile,
            cgroup_reader=lambda pid: "0::/user.slice/session-1.scope\n",
        )
        self.assertTrue(matched)

    def test_f_match_fails_cwd_outside_prefix_trap_and_symlink_outside(self) -> None:
        """Requirement f: match fails for cwd outside, /proj vs /proj-other prefix trap, and symlink resolving outside."""
        # 1. cwd clearly outside project
        outside_dir = Path(self.temp_base) / "outside"
        outside_dir.mkdir(parents=True, exist_ok=True)

        proc_outside = ProcessInfo(
            pid=124,
            ppid=1,
            starttime_ticks=1000,
            exe=Path("/usr/bin/gcc"),
            cwd=outside_dir,
            cmdline=["gcc", "test.c"],
            t_seen_monotonic=10.0,
            t_start_monotonic=9.9,
        )
        self.assertFalse(
            match(proc_outside, self.proj_dir, self.tool_profile, cgroup_reader=lambda pid: "")
        )

        # 2. /proj vs /proj-other prefix trap
        prefix_trap_dir = Path(str(self.proj_dir) + "-other")
        prefix_trap_dir.mkdir(parents=True, exist_ok=True)

        proc_prefix_trap = ProcessInfo(
            pid=125,
            ppid=1,
            starttime_ticks=1000,
            exe=Path("/usr/bin/gcc"),
            cwd=prefix_trap_dir,
            cmdline=["gcc", "test.c"],
            t_seen_monotonic=10.0,
            t_start_monotonic=9.9,
        )
        self.assertFalse(
            match(proc_prefix_trap, self.proj_dir, self.tool_profile, cgroup_reader=lambda pid: "")
        )

        # 3. Symlink inside project resolving outside project
        symlink_dir = self.proj_dir / "symlink_to_outside"
        symlink_dir.symlink_to(outside_dir, target_is_directory=True)

        proc_symlink = ProcessInfo(
            pid=126,
            ppid=1,
            starttime_ticks=1000,
            exe=Path("/usr/bin/gcc"),
            cwd=symlink_dir,
            cmdline=["gcc", "test.c"],
            t_seen_monotonic=10.0,
            t_start_monotonic=9.9,
        )
        self.assertFalse(
            match(proc_symlink, self.proj_dir, self.tool_profile, cgroup_reader=lambda pid: "")
        )

    def test_g_match_fails_for_process_already_inside_precompile_cgroup(self) -> None:
        """Requirement g: match fails for a process already inside a 'precompile' cgroup."""
        proc = ProcessInfo(
            pid=127,
            ppid=1,
            starttime_ticks=1000,
            exe=Path("/usr/bin/gcc"),
            cwd=self.proj_dir,
            cmdline=["gcc", "main.c"],
            t_seen_monotonic=10.0,
            t_start_monotonic=9.9,
        )

        # Cgroup reader returns a cgroup inside precompile
        def mock_cgroup_in_precompile(pid: int) -> str:
            return "0::/precompile/res_projectA\n"

        self.assertFalse(
            match(
                proc,
                self.proj_dir,
                self.tool_profile,
                cgroup_reader=mock_cgroup_in_precompile,
            )
        )

        # Cgroup reader returns standard user session (not precompile)
        def mock_cgroup_normal(pid: int) -> str:
            return "0::/user.slice/user-1000.slice/session-42.scope\n"

        self.assertTrue(
            match(
                proc,
                self.proj_dir,
                self.tool_profile,
                cgroup_reader=mock_cgroup_normal,
            )
        )

    def test_h_versioned_and_prefixed_names_match_and_invalid_names_do_not(self) -> None:
        """Requirement h: versioned names (gcc-13, x86_64-linux-gnu-gcc-13) match; 'gccx' does not."""
        allowed = {"gcc", "g++", "clang", "clang++", "make"}

        # Versioned compilers
        self.assertTrue(is_tool_match("gcc-13", allowed))
        self.assertTrue(is_tool_match("g++-13", allowed))
        self.assertTrue(is_tool_match("clang-18", allowed))
        self.assertTrue(is_tool_match("clang++-18", allowed))
        self.assertTrue(is_tool_match("clang-18.1", allowed))

        # Cross-compilation target prefixes
        self.assertTrue(is_tool_match("x86_64-linux-gnu-gcc-13", allowed))
        self.assertTrue(is_tool_match("aarch64-linux-gnu-g++-12", allowed))
        self.assertTrue(is_tool_match("arm-none-eabi-gcc", allowed))
        self.assertTrue(is_tool_match("x86_64-pc-linux-gnu-clang", allowed))

        # Exact matches
        self.assertTrue(is_tool_match("gcc", allowed))
        self.assertTrue(is_tool_match("make", allowed))

        # Invalid names that must not match
        self.assertFalse(is_tool_match("gccx", allowed))
        self.assertFalse(is_tool_match("mygcc", allowed))
        self.assertFalse(is_tool_match("gcc-wrapper", allowed))
        self.assertFalse(is_tool_match("makex", allowed))

    def test_i_resolve_topmost_ancestor(self) -> None:
        """Requirement i: resolve_topmost_ancestor over a synthetic process table."""
        # Case 1: make -> gcc -> cc1. When candidates are [gcc, cc1, make], returns make
        make_proc = ProcessInfo(
            pid=100, ppid=1, starttime_ticks=1000, exe=Path("/usr/bin/make"),
            cwd=self.proj_dir, cmdline=["make"], t_seen_monotonic=1.0, t_start_monotonic=1.0,
        )
        gcc_proc = ProcessInfo(
            pid=200, ppid=100, starttime_ticks=1050, exe=Path("/usr/bin/gcc"),
            cwd=self.proj_dir, cmdline=["gcc"], t_seen_monotonic=1.5, t_start_monotonic=1.5,
        )
        cc1_proc = ProcessInfo(
            pid=300, ppid=200, starttime_ticks=1100, exe=Path("/usr/lib/gcc/cc1"),
            cwd=self.proj_dir, cmdline=["cc1"], t_seen_monotonic=2.0, t_start_monotonic=2.0,
        )

        table: Dict[int, ProcessInfo] = {
            100: make_proc,
            200: gcc_proc,
            300: cc1_proc,
        }

        candidates = [gcc_proc, cc1_proc, make_proc]
        topmost = resolve_topmost_ancestor(candidates, table)
        self.assertIsNotNone(topmost)
        self.assertEqual(topmost.pid, 100)

        # Case 2: Two unrelated roots: returns the one with earlier t_start
        rootA = ProcessInfo(
            pid=500, ppid=1, starttime_ticks=5000, exe=Path("/usr/bin/gcc"),
            cwd=self.proj_dir, cmdline=["gcc"], t_seen_monotonic=5.0, t_start_monotonic=5.0,
        )
        rootB = ProcessInfo(
            pid=600, ppid=1, starttime_ticks=3000, exe=Path("/usr/bin/make"),
            cwd=self.proj_dir, cmdline=["make"], t_seen_monotonic=3.0, t_start_monotonic=3.0,
        )
        two_roots_table = {500: rootA, 600: rootB}

        earlier = resolve_topmost_ancestor([rootA, rootB], two_roots_table)
        self.assertIsNotNone(earlier)
        self.assertEqual(earlier.pid, 600)  # rootB has earlier start time (3.0 < 5.0)

    def test_j_get_descendants_returns_full_subtree_and_excludes_siblings(self) -> None:
        """Requirement j: get_descendants returns a full subtree and excludes siblings."""
        # Process hierarchy:
        #          Root (10)
        #         /         \
        #      A (20)      B (30)
        #     /      \       \
        #   A1 (40)  A2 (50) B1 (60)
        table = {
            10: 1,
            20: 10,
            30: 10,
            40: 20,
            50: 20,
            60: 30,
        }

        # Descendants of A (20) should include A1 (40) and A2 (50), but exclude B (30), B1 (60), Root (10)
        descendants_of_A = get_descendants(20, table)
        self.assertEqual(descendants_of_A, {40, 50})

        # Descendants of Root (10) should include all descendants
        descendants_of_Root = get_descendants(10, table)
        self.assertEqual(descendants_of_Root, {20, 30, 40, 50, 60})

    def test_k_live_parent_shell_spawns_fake_gcc_end_to_end(self) -> None:
        """Requirement k: parent shell spawns fake-gcc from cwd inside temp project; watcher + matcher identify it end to end."""
        fake_gcc = create_fake_tool(self.proj_dir / "bin" / "fake-gcc")
        profile = ToolProfile(name="test_live", binaries={"fake-gcc"})

        matcher = IdentityMatcher(cgroup_reader=lambda pid: "")
        matcher.register_project_target(
            project_id="test_proj",
            root_path=self.proj_dir,
            tool_profiles=[profile],
        )

        watcher = ProcessWatcher(poll_interval_normal_ms=10)
        watcher.start()
        time.sleep(0.05)

        # Parent shell spawns fake-gcc from inside project root
        shell_cmd = f"cd '{self.proj_dir}' && '{fake_gcc}' 2 & wait"
        parent_proc = subprocess.Popen(
            ["bash", "-c", shell_cmd],
            cwd=str(self.proj_dir),
        )

        matched_event: Optional[ProcessInfo] = None
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            events = watcher.poll_new_processes(timeout_ms=50)
            for ev in events:
                if matcher.matches_project(ev, "test_proj"):
                    matched_event = ev
                    break
            if matched_event is not None:
                break
            time.sleep(0.02)

        try:
            parent_proc.kill()
            parent_proc.wait()
        except Exception:
            pass
        watcher.stop()

        self.assertIsNotNone(matched_event, "fake-gcc was not identified by matcher end-to-end")
        self.assertEqual(matched_event.exe.name, "fake-gcc")
        self.assertTrue(is_path_inside(matched_event.cwd, self.proj_dir))


if __name__ == "__main__":
    unittest.main()
