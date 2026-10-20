"""Filesystem event monitor for detecting user save actions.

Stage Mapping: Stage 1 (Event Detection).
Uses inotify via ctypes on libc (inotify_init1, inotify_add_watch, inotify_rm_watch).
Detects file saves (IN_CLOSE_WRITE, IN_MOVED_TO), debounces save bursts,
filters ignored patterns (temp files, VCS, build artifacts), and suppresses saves during active builds.
"""

import ctypes
import ctypes.util
import logging
import os
import queue
import select
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from precompile.config import (
    DEFAULT_IGNORED_DIRECTORIES,
    DEFAULT_IGNORED_FILE_PATTERNS,
    MonitorConfig,
    classify_file,
    is_ignored_directory,
    is_ignored_file,
)

logger = logging.getLogger("precompile.monitor")

# --- inotify constants and flags ---
IN_CLOEXEC = 0x00080000
IN_NONBLOCK = 0x00000800

IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ONLYDIR = 0x01000000
IN_DONT_FOLLOW = 0x02000000
IN_ISDIR = 0x40000000

WATCH_MASK = IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE | IN_DONT_FOLLOW | IN_ONLYDIR
EVENT_HEADER_FORMAT = "=iIII"
EVENT_HEADER_SIZE = struct.calcsize(EVENT_HEADER_FORMAT)

# Bind libc functions via ctypes
try:
    _libc_name = ctypes.util.find_library("c") or "libc.so.6"
    _libc = ctypes.CDLL(_libc_name, use_errno=True)

    _libc.inotify_init1.argtypes = [ctypes.c_int]
    _libc.inotify_init1.restype = ctypes.c_int

    _libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    _libc.inotify_add_watch.restype = ctypes.c_int

    _libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
    _libc.inotify_rm_watch.restype = ctypes.c_int
except (OSError, AttributeError) as _e:
    _libc = None


@dataclass
class SaveEvent:
    """Represents a debounced file save event."""
    project_id: str
    path: Path
    file_class: str
    t_save_monotonic: float
    t_save_wall: float
    burst_size: int = 1

    @property
    def file_path(self) -> Path:
        """Backward compatibility alias for path."""
        return self.path

    @property
    def timestamp(self) -> float:
        """Backward compatibility alias for t_save_wall."""
        return self.t_save_wall


@dataclass
class _PendingSave:
    """Internal tracking state for debouncing in-flight saves."""
    project_id: str
    path: Path
    file_class: str
    t_save_monotonic: float
    t_save_wall: float
    burst_size: int
    deadline: float


class FileMonitor:
    """Monitors registered project roots for file save events using inotify."""

    def __init__(
        self,
        debounce_seconds: float = 0.15,
        ignored_patterns: Optional[Set[str]] = None,
        on_event: Optional[Callable[[SaveEvent], None]] = None,
        config: Optional[MonitorConfig] = None,
        on_save: Optional[Callable[[SaveEvent], None]] = None,
    ) -> None:
        """Initialize the FileMonitor.

        Args:
            debounce_seconds: Time window to coalesce rapid save bursts.
            ignored_patterns: File patterns to exclude (e.g. *.swp, *.tmp).
            on_event: Optional callback invoked when a SaveEvent is published.
            config: Optional MonitorConfig to pull defaults from.
            on_save: Alias for on_event callback.
        """
        if _libc is None:
            raise OSError("inotify via libc is required but not available on this platform")

        self.debounce_seconds = config.debounce_seconds if config is not None else debounce_seconds
        self._ignored_directories = set(
            config.ignored_directories if config is not None else DEFAULT_IGNORED_DIRECTORIES
        )
        self._ignored_file_patterns = set(
            ignored_patterns if ignored_patterns is not None
            else (config.ignored_file_patterns if config is not None else DEFAULT_IGNORED_FILE_PATTERNS)
        )
        self._on_event = on_save if on_save is not None else on_event

        self._lock = threading.RLock()
        self._projects: Dict[str, Path] = {}
        self._wd_to_path: Dict[int, Path] = {}
        self._path_to_wd: Dict[Path, int] = {}
        self._dir_to_project: Dict[Path, str] = {}
        self._pending_saves: Dict[Tuple[str, Path], _PendingSave] = {}
        self._suppressed_projects: Set[str] = set()
        self._event_queue: queue.Queue[SaveEvent] = queue.Queue()

        self._inotify_fd = _libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self._inotify_fd < 0:
            errno = ctypes.get_errno()
            raise OSError(errno, f"inotify_init1 failed: {os.strerror(errno)}")

        self._wakeup_r, self._wakeup_w = os.pipe()
        os.set_blocking(self._wakeup_r, False)
        os.set_blocking(self._wakeup_w, False)

        self._running = False
        self._stopped = False
        self._thread: Optional[threading.Thread] = None

    def _wakeup(self) -> None:
        """Send a single byte down the pipe to wake up select in the loop."""
        if self._wakeup_w >= 0:
            try:
                os.write(self._wakeup_w, b"\x01")
            except OSError:
                pass

    def register_project(self, project_id: str, root_path: Union[str, Path]) -> None:
        """Add recursive inotify watches on a project root directory.

        Args:
            project_id: Unique identifier of the project.
            root_path: Directory path of the project.
        """
        root = Path(root_path).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Project root is not a valid directory: {root}")

        with self._lock:
            self._projects[project_id] = root
            self._watch_tree(project_id, root)

    def unregister_project(self, project_id: str) -> None:
        """Remove inotify watches for a project.

        Args:
            project_id: Unique identifier of the project.
        """
        with self._lock:
            if project_id not in self._projects:
                return

            dirs_to_remove = [d for d, pid in self._dir_to_project.items() if pid == project_id]
            for d in dirs_to_remove:
                wd = self._path_to_wd.pop(d, None)
                if wd is not None:
                    self._wd_to_path.pop(wd, None)
                    if self._inotify_fd >= 0:
                        _libc.inotify_rm_watch(self._inotify_fd, wd)
                self._dir_to_project.pop(d, None)

            # Drop pending saves for this project
            keys_to_drop = [k for k in self._pending_saves if k[0] == project_id]
            for k in keys_to_drop:
                self._pending_saves.pop(k, None)

            self._suppressed_projects.discard(project_id)
            self._projects.pop(project_id, None)

        self._wakeup()

    # Convenience aliases
    watch_project = register_project
    unwatch_project = unregister_project

    def suppress_project(self, project_id: str, on: bool = True, suppress: Optional[bool] = None) -> None:
        """Suppress or unsuppress save detection while a claimed build is executing.

        Args:
            project_id: Unique identifier of the project.
            on: Whether save events should be temporarily discarded (default: True).
            suppress: Backward compatibility alias for 'on'.
        """
        is_on = on if suppress is None else suppress
        with self._lock:
            if is_on:
                self._suppressed_projects.add(project_id)
                keys_to_drop = [k for k in self._pending_saves if k[0] == project_id]
                for k in keys_to_drop:
                    self._pending_saves.pop(k, None)
            else:
                self._suppressed_projects.discard(project_id)

        self._wakeup()

    def poll_events(self, timeout_ms: int = 100) -> List[SaveEvent]:
        """Poll the event queue and return debounced save events.

        Args:
            timeout_ms: Maximum time to block waiting for events.

        Returns:
            List of debounced SaveEvent instances.
        """
        events: List[SaveEvent] = []
        timeout = (timeout_ms / 1000.0) if timeout_ms >= 0 else None
        try:
            ev = self._event_queue.get(timeout=timeout)
            events.append(ev)
            while True:
                try:
                    events.append(self._event_queue.get_nowait())
                except queue.Empty:
                    break
        except queue.Empty:
            pass
        return events

    def get_event(self, timeout: Optional[float] = None) -> Optional[SaveEvent]:
        """Wait for and return a single debounced SaveEvent, or None if timed out."""
        try:
            return self._event_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def events(self) -> queue.Queue[SaveEvent]:
        """Direct access to the underlying thread-safe SaveEvent queue."""
        return self._event_queue

    def start(self) -> None:
        """Start the background inotify monitor thread."""
        with self._lock:
            if self._running:
                return
            if self._stopped:
                raise RuntimeError("Cannot start a stopped FileMonitor instance")
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop,
                name="FileMonitorThread",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the background monitor thread and close file descriptors."""
        with self._lock:
            if not self._running and self._stopped:
                return
            self._running = False

        self._wakeup()

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            self._thread = None

        with self._lock:
            if self._inotify_fd >= 0:
                for wd in list(self._wd_to_path.keys()):
                    try:
                        _libc.inotify_rm_watch(self._inotify_fd, wd)
                    except OSError:
                        pass
                try:
                    os.close(self._inotify_fd)
                except OSError:
                    pass
                self._inotify_fd = -1

            if self._wakeup_r >= 0:
                try:
                    os.close(self._wakeup_r)
                except OSError:
                    pass
                self._wakeup_r = -1

            if self._wakeup_w >= 0:
                try:
                    os.close(self._wakeup_w)
                except OSError:
                    pass
                self._wakeup_w = -1

            self._wd_to_path.clear()
            self._path_to_wd.clear()
            self._dir_to_project.clear()
            self._pending_saves.clear()
            self._stopped = True

    def __enter__(self) -> "FileMonitor":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass

    def _watch_tree(self, project_id: str, start_path: Path) -> None:
        """Recursively add inotify watches to start_path and subdirectories."""
        project_root = self._projects.get(project_id)
        if project_root is None:
            return

        for root, dirs, _ in os.walk(start_path, followlinks=False):
            root_p = Path(root).resolve()
            try:
                rel = root_p.relative_to(project_root)
                if any(part in self._ignored_directories for part in rel.parts):
                    dirs[:] = []
                    continue
            except ValueError:
                pass

            dirs[:] = [d for d in dirs if d not in self._ignored_directories]

            if root_p not in self._path_to_wd:
                wd = _libc.inotify_add_watch(
                    self._inotify_fd,
                    str(root_p).encode("utf-8"),
                    WATCH_MASK,
                )
                if wd >= 0:
                    self._wd_to_path[wd] = root_p
                    self._path_to_wd[root_p] = wd
                    self._dir_to_project[root_p] = project_id
                else:
                    errno = ctypes.get_errno()
                    logger.debug("inotify_add_watch failed on %s: errno %d", root_p, errno)

    def _run_loop(self) -> None:
        """Main loop executed by the background thread."""
        while self._running:
            with self._lock:
                now_mono = time.monotonic()
                if self._pending_saves:
                    min_deadline = min(p.deadline for p in self._pending_saves.values())
                    timeout = max(0.0, min_deadline - now_mono)
                else:
                    timeout = None

            r_fds = [self._inotify_fd, self._wakeup_r]
            try:
                readable, _, _ = select.select(r_fds, [], [], timeout)
            except (OSError, ValueError):
                if not self._running:
                    break
                continue

            if self._wakeup_r in readable:
                try:
                    os.read(self._wakeup_r, 4096)
                except OSError:
                    pass
                if not self._running:
                    break

            if self._inotify_fd in readable:
                self._read_and_process_inotify_events()

            self._flush_expired_debounces()

    def _read_and_process_inotify_events(self) -> None:
        while True:
            try:
                buffer = os.read(self._inotify_fd, 65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError as e:
                if not self._running:
                    break
                logger.error("Error reading from inotify fd: %s", e)
                break

            if not buffer:
                break

            events = self._parse_inotify_events(buffer)
            for wd, mask, cookie, name in events:
                self._handle_single_event(wd, mask, cookie, name)

    def _parse_inotify_events(self, buffer: bytes) -> List[Tuple[int, int, int, str]]:
        events = []
        offset = 0
        buf_len = len(buffer)
        while offset + EVENT_HEADER_SIZE <= buf_len:
            wd, mask, cookie, name_len = struct.unpack_from(EVENT_HEADER_FORMAT, buffer, offset)
            offset += EVENT_HEADER_SIZE
            name = ""
            if name_len > 0:
                raw_name = buffer[offset : offset + name_len]
                offset += name_len
                null_pos = raw_name.find(b"\x00")
                if null_pos != -1:
                    raw_name = raw_name[:null_pos]
                name = raw_name.decode("utf-8", errors="replace")
            events.append((wd, mask, cookie, name))
        return events

    def _handle_single_event(self, wd: int, mask: int, cookie: int, name: str) -> None:
        if mask & IN_Q_OVERFLOW:
            logger.warning("inotify queue overflow (IN_Q_OVERFLOW); re-scanning project trees")
            with self._lock:
                for proj_id, root_path in list(self._projects.items()):
                    self._watch_tree(proj_id, root_path)
            return

        if mask & IN_IGNORED:
            with self._lock:
                dir_path = self._wd_to_path.pop(wd, None)
                if dir_path is not None:
                    self._path_to_wd.pop(dir_path, None)
                    self._dir_to_project.pop(dir_path, None)
            return

        with self._lock:
            dir_path = self._wd_to_path.get(wd)
            if dir_path is None:
                return
            project_id = self._dir_to_project.get(dir_path)
            if project_id is None:
                return
            if project_id in self._suppressed_projects:
                return
            project_root = self._projects.get(project_id)
            if project_root is None:
                return

        # Handle directory creation / movement
        if mask & IN_ISDIR:
            if mask & (IN_CREATE | IN_MOVED_TO):
                if name:
                    new_dir = dir_path / name
                    if not is_ignored_directory(new_dir, self._ignored_directories):
                        with self._lock:
                            if project_id not in self._suppressed_projects:
                                self._watch_tree(project_id, new_dir)
            return

        # Handle file save events (IN_CLOSE_WRITE and IN_MOVED_TO)
        if mask & (IN_CLOSE_WRITE | IN_MOVED_TO):
            if not name:
                return
            file_path = dir_path / name

            # Check ignore rules
            if is_ignored_file(name, self._ignored_file_patterns):
                return
            try:
                rel_path = file_path.relative_to(project_root)
            except ValueError:
                rel_path = file_path

            if any(part in self._ignored_directories for part in rel_path.parts[:-1]):
                return

            with self._lock:
                if project_id in self._suppressed_projects:
                    return

                now_mono = time.monotonic()
                now_wall = time.time()
                key = (project_id, rel_path)

                if key in self._pending_saves:
                    entry = self._pending_saves[key]
                    entry.t_save_monotonic = now_mono
                    entry.t_save_wall = now_wall
                    entry.burst_size += 1
                    entry.deadline = now_mono + self.debounce_seconds
                else:
                    file_class = classify_file(rel_path)
                    self._pending_saves[key] = _PendingSave(
                        project_id=project_id,
                        path=rel_path,
                        file_class=file_class,
                        t_save_monotonic=now_mono,
                        t_save_wall=now_wall,
                        burst_size=1,
                        deadline=now_mono + self.debounce_seconds,
                    )

    def _flush_expired_debounces(self) -> None:
        now_mono = time.monotonic()
        ready_events: List[SaveEvent] = []

        with self._lock:
            expired_keys = [
                key for key, entry in self._pending_saves.items()
                if entry.deadline <= now_mono
            ]
            for key in expired_keys:
                entry = self._pending_saves.pop(key)
                if entry.project_id not in self._suppressed_projects:
                    event = SaveEvent(
                        project_id=entry.project_id,
                        path=entry.path,
                        file_class=entry.file_class,
                        t_save_monotonic=entry.t_save_monotonic,
                        t_save_wall=entry.t_save_wall,
                        burst_size=entry.burst_size,
                    )
                    ready_events.append(event)

        for event in ready_events:
            self._event_queue.put(event)
            if self._on_event is not None:
                try:
                    self._on_event(event)
                except Exception as e:
                    logger.exception("Error executing SaveEvent callback: %s", e)


def main() -> None:
    """Manual demo entry point: python3 -m precompile.monitor <path>."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="PreCompile File Event Monitor Demo (Stage 1)")
    parser.add_argument("path", help="Directory path to monitor")
    parser.add_argument(
        "--debounce",
        type=float,
        default=0.15,
        help="Debounce window in seconds (default: 0.15)",
    )
    args = parser.parse_args()

    watch_path = Path(args.path).resolve()
    if not watch_path.is_dir():
        print(f"Error: {watch_path} is not a directory", file=sys.stderr)
        sys.exit(1)

    def on_save(ev: SaveEvent) -> None:
        wall_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ev.t_save_wall))
        print(
            f"[{wall_time_str}] SaveEvent -> project: {ev.project_id!r}, "
            f"path: {str(ev.path)!r}, class: {ev.file_class!r}, "
            f"burst: {ev.burst_size}, t_mono: {ev.t_save_monotonic:.3f}",
            flush=True,
        )

    print(f"PreCompile Monitor watching: {watch_path}", flush=True)
    print(f"Debounce window: {args.debounce}s | Press Ctrl+C to exit.", flush=True)

    monitor = FileMonitor(debounce_seconds=args.debounce, on_event=on_save)
    monitor.register_project("demo", watch_path)
    monitor.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping monitor...", flush=True)
    finally:
        monitor.stop()
        print("Monitor stopped cleanly.", flush=True)


if __name__ == "__main__":
    main()
