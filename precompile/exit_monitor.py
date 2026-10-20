"""Process exit monitoring using Linux pidfd_open and poll.

Enables sub-millisecond process exit detection without polling /proc repeatedly.
Falls back safely if os.pidfd_open is unavailable or if pidfd creation fails.
"""

import errno
import logging
import os
import select
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("precompile.daemon.exit_monitor")


class ProcessExitMonitor:
    """Monitors process exit events via Linux pidfd_open and select.poll."""

    def __init__(self, on_exit: Optional[Callable[[int, float], None]] = None) -> None:
        """Initialize ProcessExitMonitor.

        Args:
            on_exit: Callback invoked with (pid, t_exit_monotonic) when process terminates.
        """
        self.on_exit = on_exit
        self._available = hasattr(os, "pidfd_open") and hasattr(select, "poll")
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # fd -> pid mapping
        self._fd_to_pid: Dict[int, int] = {}
        self._pid_to_fd: Dict[int, int] = {}

        # Wake-up self-pipe
        self._wake_r: Optional[int] = None
        self._wake_w: Optional[int] = None
        self._poll: Optional[select.poll] = None

        if self._available:
            try:
                self._wake_r, self._wake_w = os.pipe()
                os.set_blocking(self._wake_r, False)
                os.set_blocking(self._wake_w, False)
                self._poll = select.poll()
                self._poll.register(self._wake_r, select.POLLIN)
            except Exception as e:
                logger.warning("Failed to initialize pidfd exit monitor: %s", e)
                self._available = False

    @property
    def is_available(self) -> bool:
        """Return True if pidfd_open and poll are supported and initialized."""
        return self._available

    def register_pid(self, pid: int) -> bool:
        """Register a PID for exit notification.

        Returns True if registered via pidfd, False if pidfd_open failed.
        """
        if not self._available or self._poll is None:
            return False

        with self._lock:
            if pid in self._pid_to_fd:
                return True
            try:
                # flags=0
                fd = os.pidfd_open(pid, 0)
            except OSError as e:
                # Process may have already exited (ESRCH) or permission denied
                logger.debug("pidfd_open failed for PID %d: %s", pid, e)
                return False

            self._fd_to_pid[fd] = pid
            self._pid_to_fd[pid] = fd
            self._poll.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)

            # Wake up polling thread so new fd is registered in select
            self._notify_wake()
            return True

    def unregister_pid(self, pid: int) -> None:
        """Unregister and close pidfd for a PID."""
        if not self._available or self._poll is None:
            return

        with self._lock:
            fd = self._pid_to_fd.pop(pid, None)
            if fd is not None:
                self._fd_to_pid.pop(fd, None)
                try:
                    self._poll.unregister(fd)
                except (KeyError, OSError):
                    pass
                try:
                    os.close(fd)
                except OSError:
                    pass

    def start(self) -> None:
        """Start background monitor thread."""
        if not self._available:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_poll_loop,
                daemon=True,
                name="precompile-pidfd-exit-monitor",
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop background monitor thread and close all descriptors."""
        self._stop_event.set()
        self._notify_wake()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

        with self._lock:
            for fd in list(self._fd_to_pid.keys()):
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._fd_to_pid.clear()
            self._pid_to_fd.clear()

            if self._wake_r is not None:
                try:
                    os.close(self._wake_r)
                except OSError:
                    pass
                self._wake_r = None
            if self._wake_w is not None:
                try:
                    os.close(self._wake_w)
                except OSError:
                    pass
                self._wake_w = None

    def _notify_wake(self) -> None:
        """Write a single byte to the self-pipe to break poll()."""
        if self._wake_w is not None:
            try:
                os.write(self._wake_w, b"\x00")
            except (OSError, BlockingIOError):
                pass

    def _run_poll_loop(self) -> None:
        """Worker loop polling active pidfds."""
        while not self._stop_event.is_set():
            try:
                # 100ms timeout
                events = self._poll.poll(100)
            except OSError as e:
                if e.errno == errno.EINTR:
                    continue
                logger.warning("poll() error in exit monitor: %s", e)
                break

            now_mono = time.monotonic()
            exited_pids: List[Tuple[int, float]] = []

            with self._lock:
                for fd, event in events:
                    if fd == self._wake_r:
                        # Drain wake pipe
                        try:
                            while True:
                                data = os.read(self._wake_r, 4096)
                                if not data:
                                    break
                        except (OSError, BlockingIOError):
                            pass
                        continue

                    # Process exit event on pidfd
                    pid = self._fd_to_pid.pop(fd, None)
                    if pid is not None:
                        self._pid_to_fd.pop(pid, None)
                        try:
                            self._poll.unregister(fd)
                        except (KeyError, OSError):
                            pass
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        exited_pids.append((pid, now_mono))

            for pid, exit_mono in exited_pids:
                if self.on_exit is not None:
                    try:
                        self.on_exit(pid, exit_mono)
                    except Exception as e:
                        logger.exception("Error in on_exit callback for PID %d: %s", pid, e)
