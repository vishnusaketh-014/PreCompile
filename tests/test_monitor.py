"""Unit tests for Stage 1 Filesystem Event Monitor (precompile.monitor).

All tests execute in temporary directories under /tmp (native ext4/tmpfs)
to avoid WSL DrvFs /mnt/c inotify limitations.
"""

import errno
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from precompile.config import (
    classify_file,
    is_ignored_directory,
    is_ignored_file,
)
from precompile.monitor import FileMonitor, SaveEvent


class TestFileMonitor(unittest.TestCase):
    """Test suite covering all requirements for Stage 1 FileMonitor."""

    def setUp(self) -> None:
        # All temp dirs must be created under /tmp on native Linux fs
        self.temp_dir = tempfile.mkdtemp(dir="/tmp", prefix="precompile_test_")
        self.root = Path(self.temp_dir).resolve()
        self.monitor = FileMonitor(debounce_seconds=0.10)

    def tearDown(self) -> None:
        try:
            self.monitor.stop()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_a_burst_coalesces_to_one_event_stamped_at_last_write(self) -> None:
        """Requirement a: a burst of 5 rapid writes produces exactly 1 event stamped at the last write."""
        self.monitor.register_project("proj_a", self.root)
        self.monitor.start()

        target = self.root / "main.c"

        t_last = 0.0
        for i in range(1, 6):
            time.sleep(0.015)
            target.write_text(f"version {i}")
            t_last = time.monotonic()

        # Wait for debounce window (0.10s) to settle
        time.sleep(0.25)

        events = self.monitor.poll_events(timeout_ms=100)
        self.assertEqual(len(events), 1, f"Expected 1 event, got {len(events)}")
        ev = events[0]
        self.assertEqual(ev.project_id, "proj_a")
        self.assertEqual(ev.path, Path("main.c"))
        self.assertEqual(ev.file_class, "source")
        self.assertEqual(ev.burst_size, 5)
        # Verify stamped monotonic timestamp is within tolerance of the last write
        self.assertAlmostEqual(ev.t_save_monotonic, t_last, delta=0.03)

    def test_b_spaced_saves_produce_two_events(self) -> None:
        """Requirement b: two saves more than the debounce window apart produce 2 events."""
        self.monitor.register_project("proj_b", self.root)
        self.monitor.start()

        target = self.root / "main.c"

        # Save 1
        target.write_text("first save")
        # Wait for first debounce window (0.10s) to expire
        time.sleep(0.25)

        # Save 2
        target.write_text("second save")
        time.sleep(0.25)

        events = self.monitor.poll_events(timeout_ms=100)
        self.assertEqual(len(events), 2, f"Expected 2 events, got {len(events)}")
        self.assertEqual(events[0].burst_size, 1)
        self.assertEqual(events[1].burst_size, 1)
        self.assertLess(events[0].t_save_monotonic, events[1].t_save_monotonic)

    def test_c_writes_inside_ignored_directories_produce_no_events(self) -> None:
        """Requirement c: writes inside .git/ and build/ produce no events."""
        git_dir = self.root / ".git"
        build_dir = self.root / "build"
        git_dir.mkdir()
        build_dir.mkdir()

        self.monitor.register_project("proj_c", self.root)
        self.monitor.start()

        # Write inside .git and build
        (git_dir / "HEAD").write_text("ref: refs/heads/main")
        (git_dir / "index").write_text("git index data")
        (build_dir / "output.o").write_text("binary object")
        (build_dir / "generated.c").write_text("generated source")

        time.sleep(0.25)
        events = self.monitor.poll_events(timeout_ms=100)
        self.assertEqual(len(events), 0, f"Expected 0 events, got {events}")

    def test_d_editor_temp_and_swap_files_produce_no_events(self) -> None:
        """Requirement d: editor temp/swap files produce no events."""
        self.monitor.register_project("proj_d", self.root)
        self.monitor.start()

        ignored_files = [
            "main.c.swp",
            "main.c.swx",
            "main.c~",
            ".#main.c",
            "#main.c#",
            "4913",  # vim probe file
            "4914",
            "scratch.tmp",
            "test.o",
            "test.d",
            "libtest.a",
            "libtest.so",
            "test.pyc",
        ]
        for name in ignored_files:
            (self.root / name).write_text("temporary data")

        time.sleep(0.25)
        events = self.monitor.poll_events(timeout_ms=100)
        self.assertEqual(len(events), 0, f"Expected 0 events, got {events}")

    def test_e_atomic_save_pattern_produces_single_event_for_target(self) -> None:
        """Requirement e: atomic-save pattern (write temp file, rename over target) produces exactly 1 event for the target."""
        self.monitor.register_project("proj_e", self.root)
        self.monitor.start()

        target = self.root / "main.c"
        target.write_text("initial")
        # Drain the initial creation event
        time.sleep(0.25)
        self.monitor.poll_events(timeout_ms=50)

        # Atomic save simulation: write to temp file, then atomic rename
        temp_file = self.root / "main.c.tmp"
        temp_file.write_text("atomic write replacement")
        os.replace(temp_file, target)

        time.sleep(0.25)
        events = self.monitor.poll_events(timeout_ms=100)
        self.assertEqual(len(events), 1, f"Expected 1 event, got {len(events)}")
        ev = events[0]
        self.assertEqual(ev.path, Path("main.c"))
        self.assertEqual(ev.file_class, "source")

    def test_f_subdirectory_created_after_start_is_watched(self) -> None:
        """Requirement f: a subdirectory created after start() is watched."""
        self.monitor.register_project("proj_f", self.root)
        self.monitor.start()

        # Create new subdirectory after start()
        new_sub = self.root / "src"
        new_sub.mkdir()
        time.sleep(0.05)  # Allow inotify IN_CREATE to add watch

        # Write file inside the new subdirectory
        file_in_sub = new_sub / "module.cpp"
        file_in_sub.write_text("int run() { return 0; }")

        time.sleep(0.25)
        events = self.monitor.poll_events(timeout_ms=100)
        self.assertEqual(len(events), 1, f"Expected 1 event, got {len(events)}")
        ev = events[0]
        self.assertEqual(ev.path, Path("src/module.cpp"))
        self.assertEqual(ev.file_class, "source")

    def test_g_suppress_project_drops_and_resumes_saves(self) -> None:
        """Requirement g: suppress_project(True) drops saves, and suppress_project(False) resumes them."""
        self.monitor.register_project("proj_g", self.root)
        self.monitor.start()

        target = self.root / "main.c"

        # Suppress project
        self.monitor.suppress_project("proj_g", on=True)
        target.write_text("save during build - should be dropped")
        time.sleep(0.25)

        events = self.monitor.poll_events(timeout_ms=100)
        self.assertEqual(len(events), 0, "Expected saves to be dropped while suppressed")

        # Resume project
        self.monitor.suppress_project("proj_g", on=False)
        target.write_text("save after build - should be caught")
        time.sleep(0.25)

        events = self.monitor.poll_events(timeout_ms=100)
        self.assertEqual(len(events), 1, "Expected saves to resume when unsuppressed")
        self.assertEqual(events[0].path, Path("main.c"))

    def test_h_file_class_classification(self) -> None:
        """Requirement h: file_class classification for .c, .h, .txt."""
        # Test classifier helper
        self.assertEqual(classify_file("test.c"), "source")
        self.assertEqual(classify_file("test.cc"), "source")
        self.assertEqual(classify_file("test.cpp"), "source")
        self.assertEqual(classify_file("test.cxx"), "source")

        self.assertEqual(classify_file("test.h"), "header")
        self.assertEqual(classify_file("test.hh"), "header")
        self.assertEqual(classify_file("test.hpp"), "header")
        self.assertEqual(classify_file("test.hxx"), "header")

        self.assertEqual(classify_file("test.txt"), "other")
        self.assertEqual(classify_file("Makefile"), "other")

        # Test live SaveEvents from monitor
        self.monitor.register_project("proj_h", self.root)
        self.monitor.start()

        (self.root / "file.c").write_text("int a = 1;")
        (self.root / "file.h").write_text("#pragma once")
        (self.root / "file.txt").write_text("notes")

        time.sleep(0.25)
        events = self.monitor.poll_events(timeout_ms=100)
        classes = {ev.path.name: ev.file_class for ev in events}

        self.assertEqual(classes.get("file.c"), "source")
        self.assertEqual(classes.get("file.h"), "header")
        self.assertEqual(classes.get("file.txt"), "other")

    def test_i_stop_leaves_no_extra_threads_and_closes_inotify_fd(self) -> None:
        """Requirement i: stop() leaves no extra threads and closes the inotify fd."""
        monitor = FileMonitor(debounce_seconds=0.10)
        monitor.register_project("proj_i", self.root)
        monitor.start()

        self.assertTrue(monitor._thread.is_alive())
        inotify_fd = monitor._inotify_fd
        self.assertGreater(inotify_fd, 0)

        # Check thread exists before stop
        threads_running = [t for t in threading.enumerate() if t.name == "FileMonitorThread"]
        self.assertEqual(len(threads_running), 1)

        # Stop monitor
        monitor.stop()

        # Thread must have terminated
        threads_after = [t for t in threading.enumerate() if t.name == "FileMonitorThread"]
        self.assertEqual(len(threads_after), 0)

        # Inotify fd must be closed
        with self.assertRaises(OSError) as ctx:
            os.fstat(inotify_fd)
        self.assertEqual(ctx.exception.errno, errno.EBADF)

    def test_config_ignore_rules(self) -> None:
        """Verify config ignore rules and helper functions directly."""
        # Directories
        self.assertTrue(is_ignored_directory(".git"))
        self.assertTrue(is_ignored_directory("build"))
        self.assertTrue(is_ignored_directory("target"))
        self.assertTrue(is_ignored_directory(Path("build/obj")))
        self.assertFalse(is_ignored_directory("src"))
        self.assertFalse(is_ignored_directory(Path("src/components")))

        # Files
        self.assertTrue(is_ignored_file("4913"))
        self.assertTrue(is_ignored_file("4914"))
        self.assertTrue(is_ignored_file("file.swp"))
        self.assertTrue(is_ignored_file("file.swx"))
        self.assertTrue(is_ignored_file("file~"))
        self.assertTrue(is_ignored_file(".#file"))
        self.assertTrue(is_ignored_file("#file#"))
        self.assertTrue(is_ignored_file("file.tmp"))
        self.assertTrue(is_ignored_file("file.o"))
        self.assertTrue(is_ignored_file("file.d"))
        self.assertTrue(is_ignored_file("lib.a"))
        self.assertTrue(is_ignored_file("lib.so"))
        self.assertTrue(is_ignored_file("file.pyc"))

        self.assertFalse(is_ignored_file("main.c"))
        self.assertFalse(is_ignored_file("main.h"))
        self.assertFalse(is_ignored_file("doc.txt"))


if __name__ == "__main__":
    unittest.main()
