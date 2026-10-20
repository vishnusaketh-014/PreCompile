"""Process creation watcher for detecting newly spawned processes.

Stage Mapping: Stage 2 (Process-Creation Observation and Identity Matching).

This module polls Linux /proc at high frequency to discover newly spawned processes,
extracting their PID, PPID, executable path, working directory, and command-line arguments.

Note on Polling vs Netlink:
    Polling /proc can miss very short-lived processes (e.g. commands that run and exit
    within a few milliseconds between poll ticks). A netlink process connector (cn_proc)
    backend provides event-driven zero-latency spawn detection and is designed as a later
    upgrade behind this exact same ProcessWatcher interface.
"""

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("precompile.watcher")


@dataclass
class ProcessInfo:
    """Represents a newly detected process spawn event and its process metadata."""
    pid: int
    ppid: int
    starttime_ticks: int
    exe: Path
    cwd: Path
    cmdline: List[str]
    t_seen_monotonic: float
    t_start_monotonic: float
    t_start_source: str = "boottime"  # 'boottime' or 'seen_monotonic'
    exec_of_known_pid: bool = False

    @property
    def executable(self) -> Path:
        """Backward-compatibility alias for exe."""
        return self.exe

    @property
    def exe_name(self) -> str:
        """Name of the executable binary."""
        return self.exe.name if self.exe is not None else ""

    @property
    def timestamp(self) -> float:
        """Backward-compatibility alias for t_start_monotonic."""
        return self.t_start_monotonic


# Backward compatibility alias for the skeleton interface
ProcessSpawnEvent = ProcessInfo


@dataclass
class WatcherStats:
    """Monitoring and overhead statistics for evaluation."""
    scans_performed: int = 0
    total_scan_duration_s: float = 0.0
    max_scan_duration_s: float = 0.0
    processes_seen: int = 0
    processes_missed: int = 0
    scans_normal: int = 0
    scans_intensified: int = 0
    time_normal_s: float = 0.0
    time_intensified_s: float = 0.0

    @property
    def avg_scan_duration_s(self) -> float:
        """Average scan duration in seconds."""
        if self.scans_performed == 0:
            return 0.0
        return self.total_scan_duration_s / self.scans_performed

    @property
    def avg_scan_duration_ms(self) -> float:
        """Average scan duration in milliseconds."""
        return self.avg_scan_duration_s * 1000.0

    @property
    def max_scan_duration_ms(self) -> float:
        """Maximum scan duration in milliseconds."""
        return self.max_scan_duration_s * 1000.0

    @property
    def scans_per_sec_normal(self) -> float:
        """Scan rate in normal mode (scans / sec)."""
        if self.time_normal_s <= 0.0:
            return 0.0
        return self.scans_normal / self.time_normal_s

    @property
    def scans_per_sec_intensified(self) -> float:
        """Scan rate in intensified mode (scans / sec)."""
        if self.time_intensified_s <= 0.0:
            return 0.0
        return self.scans_intensified / self.time_intensified_s


def parse_stat_content(stat_content: str) -> Tuple[int, int, int, str]:
    """Parse /proc/<pid>/stat line handling comm with spaces and parentheses.

    Returns:
        Tuple of (pid, ppid, starttime_ticks, comm).

    Raises:
        ValueError: If stat content format is invalid.
    """
    r_paren = stat_content.rfind(")")
    l_paren = stat_content.find("(")
    if r_paren == -1 or l_paren == -1 or r_paren <= l_paren:
        raise ValueError("Invalid stat line format: missing parentheses around comm")

    pid_str = stat_content[:l_paren].strip()
    pid = int(pid_str)
    comm = stat_content[l_paren + 1:r_paren]

    # Fields after ')' are 0-indexed relative to field 3 (state)
    # Field 4 (ppid) is at index 1
    # Field 22 (starttime) is at index 19
    rest = stat_content[r_paren + 1:].strip().split()
    if len(rest) < 20:
        raise ValueError(f"Invalid stat line: insufficient fields after comm ({len(rest)})")

    ppid = int(rest[1])
    starttime_ticks = int(rest[19])
    return pid, ppid, starttime_ticks, comm


def compute_t_start_monotonic(
    starttime_ticks: int,
    t_seen_monotonic: float,
    clk_tck: Optional[int] = None,
) -> Tuple[float, str]:
    """Compute t_start_monotonic from starttime_ticks against CLOCK_BOOTTIME.

    Linux /proc/<pid>/stat field 22 (starttime) measures time since system boot
    in clock ticks (CLK_TCK). We calculate t_start_boottime, then convert to
    the time.monotonic() timebase using the offset between CLOCK_BOOTTIME and
    CLOCK_MONOTONIC.

    Returns:
        Tuple of (t_start_monotonic, source) where source is 'boottime' or 'seen_monotonic'.
    """
    try:
        if clk_tck is None:
            clk_tck = os.sysconf(os.sysconf_names["SC_CLK_TCK"])

        t_boot = time.clock_gettime(time.CLOCK_BOOTTIME)
        t_mono = time.monotonic()
        t_start_boottime = starttime_ticks / clk_tck
        # Offset: time the system was suspended (CLOCK_BOOTTIME - CLOCK_MONOTONIC)
        # Therefore: t_start_monotonic = t_start_boottime - (t_boot - t_mono)
        t_start_monotonic = t_start_boottime - (t_boot - t_mono)
        return t_start_monotonic, "boottime"
    except Exception as e:
        logger.debug("Failed to compute t_start_monotonic from boottime: %s", e)
        return t_seen_monotonic, "seen_monotonic"


class ProcessWatcher:
    """Watches the operating system for newly created processes via /proc polling."""

    def __init__(
        self,
        mode: str = "proc_poll",
        poll_interval_ms: Optional[int] = None,
        poll_interval_normal_ms: int = 50,
        poll_interval_intensified_ms: int = 10,
        on_spawn: Optional[Callable[[ProcessInfo], None]] = None,
        on_batch: Optional[Callable[[List[ProcessInfo]], None]] = None,
        proc_path: Union[str, Path] = "/proc",
        baseline_existing: bool = True,
        grace_period_seconds: float = 5.0,
        max_table_size: int = 20000,
    ) -> None:
        """Initialize the ProcessWatcher.

        Args:
            mode: Detection mode ('proc_poll' supported, 'netlink_cn_proc' reserved for upgrade).
            poll_interval_ms: Optional override for normal polling interval.
            poll_interval_normal_ms: Default polling interval in normal mode (50 ms).
            poll_interval_intensified_ms: Polling interval in intensified mode (10 ms).
            on_spawn: Optional callback invoked when a new process is discovered.
            on_batch: Optional callback invoked with the full list of newly discovered processes per scan batch.
            proc_path: Filesystem path to proc directory (default: '/proc').
            baseline_existing: If True, existing PIDs are baselined on start/init
                so they are not reported as new spawns.
            grace_period_seconds: Grace period in seconds to retain dead PIDs for ancestor walks (default 5.0s).
            max_table_size: Maximum entries in process table before oldest dead entries are evicted (default 20000).
        """
        if mode != "proc_poll":
            raise NotImplementedError(
                f"Mode '{mode}' is not implemented. Use 'proc_poll' (netlink is a planned upgrade)."
            )

        self.mode = mode
        self.poll_interval_normal_ms = (
            poll_interval_ms if poll_interval_ms is not None else poll_interval_normal_ms
        )
        self.poll_interval_intensified_ms = poll_interval_intensified_ms
        self._on_spawn = on_spawn
        self._on_batch = on_batch
        self._proc_path = Path(proc_path)
        self._baseline_existing = baseline_existing
        self.grace_period_seconds = grace_period_seconds
        self.max_table_size = max_table_size

        self._intensified = False
        self._last_mode_change_mono = time.monotonic()
        self._known_pids: Set[int] = set()
        self._parent_map: Dict[int, int] = {}
        self._process_table: Dict[int, ProcessInfo] = {}
        self._last_seen_alive: Dict[int, float] = {}
        self._first_seen: Dict[int, float] = {}

        self._stats = WatcherStats()
        self._queue: queue.Queue[ProcessInfo] = queue.Queue()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._initialized = False

    def set_intensified(self, intensified: bool) -> None:
        """Switch polling interval between normal mode and intensified mode.

        Rule: Active reservations intensify the watcher across all conditions
        (C1-reactive, C2-full, C3-static) to ensure millisecond-level responsiveness
        for descendant tracking and completion detection.
        """
        with self._lock:
            val = bool(intensified)
            if val != self._intensified:
                now = time.monotonic()
                dt = max(0.0, now - self._last_mode_change_mono)
                if self._intensified:
                    self._stats.time_intensified_s += dt
                else:
                    self._stats.time_normal_s += dt
                self._last_mode_change_mono = now
                self._intensified = val

    def is_intensified(self) -> bool:
        """Return True if watcher is currently operating in intensified mode."""
        with self._lock:
            return self._intensified

    def init_known_pids(self) -> None:
        """Baseline currently existing PIDs in /proc so they are not treated as new."""
        current_pids: Set[int] = set()
        try:
            for name in os.listdir(self._proc_path):
                if name.isdigit():
                    current_pids.add(int(name))
        except (FileNotFoundError, PermissionError):
            pass

        with self._lock:
            self._known_pids = current_pids
            self._initialized = True

    def start(self) -> None:
        """Start listening for process creation events in a background thread."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._baseline_existing and not self._initialized:
                self.init_known_pids()
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_poll_loop,
                daemon=True,
                name="precompile-process-watcher",
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop listening for process creation events and join worker thread cleanly."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def poll_new_processes(self, timeout_ms: int = 0) -> List[ProcessInfo]:
        """Query newly spawned processes since the last poll.

        Args:
            timeout_ms: Optional millisecond timeout to wait if queue is currently empty.

        Returns:
            List of newly discovered ProcessInfo instances.
        """
        events: List[ProcessInfo] = []
        if timeout_ms > 0 and self._queue.empty():
            try:
                item = self._queue.get(timeout=timeout_ms / 1000.0)
                events.append(item)
            except queue.Empty:
                return events

        while True:
            try:
                item = self._queue.get_nowait()
                events.append(item)
            except queue.Empty:
                break
        return events

    def poll_events(self, timeout_ms: int = 0) -> List[ProcessInfo]:
        """Alias for poll_new_processes matching FileMonitor interface."""
        return self.poll_new_processes(timeout_ms=timeout_ms)

    def get_child_pids(self, parent_pid: int) -> Set[int]:
        """Retrieve all currently active child/descendant PIDs of a given parent PID."""
        with self._lock:
            parent_map = dict(self._parent_map)
        from precompile.matcher import get_descendants
        return get_descendants(parent_pid, parent_map)

    def get_stats(self) -> WatcherStats:
        """Return a snapshot copy of watcher monitoring and overhead statistics."""
        with self._lock:
            now = time.monotonic()
            dt = max(0.0, now - self._last_mode_change_mono)
            time_normal = self._stats.time_normal_s + (0.0 if self._intensified else dt)
            time_intensified = self._stats.time_intensified_s + (dt if self._intensified else 0.0)
            return WatcherStats(
                scans_performed=self._stats.scans_performed,
                total_scan_duration_s=self._stats.total_scan_duration_s,
                max_scan_duration_s=self._stats.max_scan_duration_s,
                processes_seen=self._stats.processes_seen,
                processes_missed=self._stats.processes_missed,
                scans_normal=self._stats.scans_normal,
                scans_intensified=self._stats.scans_intensified,
                time_normal_s=time_normal,
                time_intensified_s=time_intensified,
            )

    def get_process_table(self) -> Dict[int, ProcessInfo]:
        """Return a snapshot copy of the discovered process table."""
        with self._lock:
            return dict(self._process_table)

    def get_parent_map(self) -> Dict[int, int]:
        """Return a snapshot copy of the parent PID map (pid -> ppid)."""
        with self._lock:
            return dict(self._parent_map)

    def scan_once(self) -> List[ProcessInfo]:
        """Perform a single scan of /proc and return newly discovered processes."""
        t_start = time.perf_counter()
        discovered = self._scan_proc()
        dur = time.perf_counter() - t_start

        with self._lock:
            self._stats.scans_performed += 1
            if self._intensified:
                self._stats.scans_intensified += 1
            else:
                self._stats.scans_normal += 1
            self._stats.total_scan_duration_s += dur
            if dur > self._stats.max_scan_duration_s:
                self._stats.max_scan_duration_s = dur

        for proc in discovered:
            self._queue.put(proc)
            if self._on_spawn is not None:
                try:
                    self._on_spawn(proc)
                except Exception as e:
                    logger.exception("Error executing on_spawn callback: %s", e)

        if self._on_batch is not None and discovered:
            try:
                self._on_batch(discovered)
            except Exception as e:
                logger.exception("Error executing on_batch callback: %s", e)

        return discovered

    def _run_poll_loop(self) -> None:
        """Internal background polling loop executed in worker thread."""
        while not self._stop_event.is_set():
            self.scan_once()

            interval_ms = (
                self.poll_interval_intensified_ms
                if self._intensified
                else self.poll_interval_normal_ms
            )
            # Sleep using event wait so stop() wakes immediately
            self._stop_event.wait(timeout=interval_ms / 1000.0)

    def _scan_proc(self) -> List[ProcessInfo]:
        """List numeric directories in /proc and inspect new PIDs."""
        current_pids: Set[int] = set()
        try:
            entries = os.listdir(self._proc_path)
        except (FileNotFoundError, PermissionError):
            return []

        for name in entries:
            if name.isdigit():
                current_pids.add(int(name))

        now_mono = time.monotonic()
        with self._lock:
            # 1. Update last_seen_alive and prune entries whose grace period expired
            for pid in list(self._process_table.keys()):
                if pid in current_pids:
                    self._last_seen_alive[pid] = now_mono
                else:
                    last_alive = self._last_seen_alive.get(pid, now_mono)
                    if now_mono - last_alive > self.grace_period_seconds:
                        self._process_table.pop(pid, None)
                        self._parent_map.pop(pid, None)
                        self._last_seen_alive.pop(pid, None)
                        self._first_seen.pop(pid, None)

            if not self._initialized:
                if self._baseline_existing:
                    self._known_pids = current_pids
                    self._initialized = True
                    return []
                else:
                    self._initialized = True

            new_pids = current_pids - self._known_pids
            # Prune exited PIDs from known_pids so PID recycling is handled
            self._known_pids = current_pids

        discovered: List[ProcessInfo] = []
        for pid in sorted(new_pids):
            proc_info = self._inspect_pid(pid)
            with self._lock:
                if proc_info is not None:
                    self._stats.processes_seen += 1
                    self._parent_map[pid] = proc_info.ppid
                    self._process_table[pid] = proc_info
                    self._last_seen_alive[pid] = now_mono
                    self._first_seen[pid] = now_mono
                    discovered.append(proc_info)
                else:
                    self._stats.processes_missed += 1

        # Check recently seen PIDs (first seen within last ~0.5s) for exec-in-place
        recheck_pids = []
        with self._lock:
            for pid, first_t in list(self._first_seen.items()):
                if pid in current_pids and (now_mono - first_t <= 0.50):
                    old_info = self._process_table.get(pid)
                    if old_info is not None:
                        recheck_pids.append((pid, str(old_info.exe)))

        for pid, old_exe_str in recheck_pids:
            exe_link = self._proc_path / str(pid) / "exe"
            try:
                target = os.readlink(exe_link)
                if target.endswith(" (deleted)"):
                    target = target[:-10]
                if target != old_exe_str:
                    # Exec detected!
                    new_proc = self._inspect_pid(pid)
                    if new_proc is not None:
                        new_proc.exec_of_known_pid = True
                        with self._lock:
                            self._process_table[pid] = new_proc
                            self._parent_map[pid] = new_proc.ppid
                        discovered.append(new_proc)
                        logger.debug("Exec in place detected for PID %d: %s -> %s", pid, old_exe_str, target)
            except OSError:
                pass

        with self._lock:
            # 2. Hard cap enforcement: evict oldest dead entries first, then oldest overall
            if len(self._process_table) > self.max_table_size:
                dead_entries = [
                    (pid, self._last_seen_alive.get(pid, 0.0))
                    for pid in list(self._process_table.keys())
                    if pid not in current_pids
                ]
                dead_entries.sort(key=lambda x: x[1])
                excess = len(self._process_table) - self.max_table_size
                for pid, _ in dead_entries[:excess]:
                    self._process_table.pop(pid, None)
                    self._parent_map.pop(pid, None)
                    self._last_seen_alive.pop(pid, None)
                    self._first_seen.pop(pid, None)

                # If still exceeding cap, evict oldest overall
                if len(self._process_table) > self.max_table_size:
                    excess = len(self._process_table) - self.max_table_size
                    all_entries = sorted(
                        self._process_table.keys(),
                        key=lambda p: self._last_seen_alive.get(p, 0.0),
                    )
                    for pid in all_entries[:excess]:
                        self._process_table.pop(pid, None)
                        self._parent_map.pop(pid, None)
                        self._last_seen_alive.pop(pid, None)
                        self._first_seen.pop(pid, None)

        return discovered

    def _inspect_pid(self, pid: int) -> Optional[ProcessInfo]:
        """Read and parse proc entries for a single PID.

        Returns ProcessInfo if successful, or None if process exited mid-read
        or cannot be accessed due to permissions.
        """
        t_seen = time.monotonic()
        pid_dir = self._proc_path / str(pid)
        stat_file = pid_dir / "stat"
        exe_link = pid_dir / "exe"
        cwd_link = pid_dir / "cwd"
        cmdline_file = pid_dir / "cmdline"

        try:
            # 1. Read /proc/<pid>/stat
            with open(stat_file, "r", encoding="utf-8") as f:
                stat_content = f.read()

            pid_val, ppid, starttime_ticks, _ = parse_stat_content(stat_content)

            # 2. Read /proc/<pid>/exe
            exe_target = os.readlink(exe_link)
            if exe_target.endswith(" (deleted)"):
                exe_target = exe_target[:-10]
            exe = Path(exe_target)

            # 3. Read /proc/<pid>/cwd
            cwd_target = os.readlink(cwd_link)
            if cwd_target.endswith(" (deleted)"):
                cwd_target = cwd_target[:-10]
            cwd = Path(cwd_target)

            # 4. Read /proc/<pid>/cmdline
            with open(cmdline_file, "rb") as f:
                raw_cmd = f.read()
            if raw_cmd:
                cmdline = [
                    arg.decode("utf-8", errors="replace")
                    for arg in raw_cmd.split(b"\x00")
                    if arg
                ]
            else:
                cmdline = []

            # 5. Compute t_start_monotonic
            t_start, source = compute_t_start_monotonic(starttime_ticks, t_seen)

            return ProcessInfo(
                pid=pid_val,
                ppid=ppid,
                starttime_ticks=starttime_ticks,
                exe=exe,
                cwd=cwd,
                cmdline=cmdline,
                t_seen_monotonic=t_seen,
                t_start_monotonic=t_start,
                t_start_source=source,
            )
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            return None
        except (ValueError, OSError):
            return None
