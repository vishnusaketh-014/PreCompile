"""Safety rules, bounded harm enforcement, and privilege fallbacks.

Stage Mapping: Stage 10 (Safety, Bounded Harm, and Fallback).
Ensures fair scheduling (fair-share CFS only), caps weights, bounds boost lifetimes,
budgets cumulative high-priority time, cleans stale cgroups at boot, provides kill-switch
and circuit breaker mechanisms, and detects lack of root privilege to degrade gracefully
to observation-only mode.
"""

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

logger = logging.getLogger("precompile.safety")

DEFAULT_MAX_WEIGHT = 1000
DEFAULT_MAX_BOOST_SECONDS = 120.0
DEFAULT_HOURLY_BUDGET_SECONDS = 900.0
DEFAULT_BUDGET_INTERVAL_S = 3600.0
DEFAULT_PAUSE_FILE = Path("/var/run/precompile.pause")
FALLBACK_PAUSE_FILE = Path("/run/precompile.pause")


def check_process_scheduling(pid: int) -> bool:
    """Inspect a process's scheduling policy.

    Returns True if the process uses a fair-share scheduling class (SCHED_OTHER,
    SCHED_BATCH, or SCHED_IDLE). If the process is using a real-time scheduling class
    (SCHED_FIFO, SCHED_RR, SCHED_DEADLINE), emits a warning and returns False so that
    PreCompile will refuse to touch its priority.
    """
    try:
        if hasattr(os, "sched_getscheduler"):
            policy = os.sched_getscheduler(pid)
            # Linux fair scheduling classes:
            # 0: SCHED_OTHER / SCHED_NORMAL
            # 3: SCHED_BATCH
            # 5: SCHED_IDLE
            if policy not in (0, 3, 5):
                logger.warning(
                    "[safety] Process %d has non-fair scheduling policy %d; refusing to touch priority",
                    pid,
                    policy,
                )
                return False
            return True
        else:
            # Fallback for platforms without os.sched_getscheduler: inspect /proc/<pid>/stat field 41
            stat_path = Path(f"/proc/{pid}/stat")
            if stat_path.exists():
                text = stat_path.read_text()
                rparen = text.rfind(")")
                if rparen != -1:
                    fields = text[rparen + 2:].split()
                    if len(fields) > 38:
                        policy = int(fields[38])
                        if policy not in (0, 3, 5):
                            logger.warning(
                                "[safety] Process %d has non-fair scheduling policy %d; refusing to touch priority",
                                pid,
                                policy,
                            )
                            return False
    except (ProcessLookupError, FileNotFoundError, PermissionError):
        pass
    except Exception as e:
        logger.debug("[safety] Unable to inspect scheduling policy for pid %d: %s", pid, e)
    return True


class SafetyGovernor:
    """Enforces system invariants and resource budgets to prevent system harm."""

    def __init__(
        self,
        max_weight: int = 1000,
        max_boost_seconds: float = 120.0,
        hourly_budget_seconds: float = 900.0,
        budget_interval_s: float = 3600.0,
        cgroup_root: Path = Path("/sys/fs/cgroup/precompile"),
        pause_file: Optional[Path] = None,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_cooldown: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize SafetyGovernor with limits.

        Args:
            max_weight: Maximum allowable CFS CPU weight (capped at 1000).
            max_boost_seconds: Maximum lifetime for any single reservation.
            hourly_budget_seconds: Maximum total boosted seconds allowed in rolling interval.
            budget_interval_s: Rolling budget window in seconds (default 3600s = 1 hour).
            cgroup_root: Root path for precompile cgroups.
            pause_file: Path to kill-switch pause file.
            circuit_breaker_threshold: Consecutive actuation failures before tripping breaker.
            circuit_breaker_cooldown: Cooldown duration in seconds when circuit breaker trips.
            clock: Monotonic clock provider.
        """
        if max_weight > 1000:
            logger.warning(
                "[safety] Configured max_weight %d exceeds system ceiling 1000; clamping to 1000",
                max_weight,
            )
            max_weight = 1000
        elif max_weight < 1:
            logger.warning("[safety] Configured max_weight %d < 1; clamping to 1", max_weight)
            max_weight = 1

        self.max_weight = max_weight
        self.max_boost_seconds = max_boost_seconds
        self.hourly_budget_seconds = hourly_budget_seconds
        self.budget_interval_s = budget_interval_s
        self.cgroup_root = Path(cgroup_root)
        self.pause_file = Path(pause_file) if pause_file else DEFAULT_PAUSE_FILE
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_cooldown = circuit_breaker_cooldown
        self.clock = clock

        self.last_degradation_reason: Optional[str] = None
        self.consecutive_actuation_errors: int = 0
        self.circuit_breaker_until: Optional[float] = None
        self._in_memory_budget_history: List[Tuple[float, float]] = []

    def check_privileges(self, cgroup_path: Optional[Path] = None) -> bool:
        """Check if daemon possesses sufficient privileges (root / cgroup v2 write).

        Returns:
            True if privileged, False if system must degrade to observation-only.
        """
        # 1. Root check
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            self.last_degradation_reason = "not_root"
            logger.warning(
                "[safety] Daemon running as non-root (uid=%d); degrading to observation-only mode",
                os.geteuid(),
            )
            return False

        # 2. Cgroup v2 unified hierarchy check
        controllers_file = Path("/sys/fs/cgroup/cgroup.controllers")
        if not controllers_file.exists():
            self.last_degradation_reason = "cgroup_v1"
            logger.warning(
                "[safety] Cgroup v2 unified hierarchy not found (/sys/fs/cgroup/cgroup.controllers missing); "
                "degrading to observation-only mode"
            )
            return False

        # 3. CPU controller enabled check
        try:
            available_controllers = controllers_file.read_text().split()
            if "cpu" not in available_controllers:
                self.last_degradation_reason = "no_cpu_controller"
                logger.warning(
                    "[safety] CPU controller not enabled in cgroup.controllers; degrading to observation-only mode"
                )
                return False
        except Exception as e:
            self.last_degradation_reason = "no_cpu_controller"
            logger.warning(
                "[safety] Failed to read cgroup.controllers (%s); degrading to observation-only mode", e
            )
            return False

        # 4. Target/parent cgroup writable check
        target_root = cgroup_path if cgroup_path is not None else self.cgroup_root
        test_dir = target_root if target_root.exists() else target_root.parent
        if not os.access(test_dir, os.W_OK):
            self.last_degradation_reason = "parent_not_writable"
            logger.warning(
                "[safety] Cgroup directory %s is not writable; degrading to observation-only mode",
                test_dir,
            )
            return False

        self.last_degradation_reason = None
        return True

    def clamp_weight(self, requested_weight: int) -> int:
        """Ensure requested CPU weight does not exceed safety ceiling.

        Args:
            requested_weight: Requested CFS weight.

        Returns:
            Clamped weight (1 <= weight <= max_weight).
        """
        clamped = max(1, min(self.max_weight, int(requested_weight)))
        if clamped != requested_weight:
            logger.warning(
                "[safety] Requested weight %d clamped to safety bounds [%d, %d] -> %d",
                requested_weight,
                1,
                self.max_weight,
                clamped,
            )
        return clamped

    def check_and_consume_budget(
        self,
        duration_seconds: float,
        store: Optional[Any] = None,
        now: Optional[float] = None,
    ) -> bool:
        """Check if duration is within the rolling hourly budget and consume it.

        Args:
            duration_seconds: Requested reservation duration.
            store: Optional DataStore instance for database-persisted budget tracking.
            now: Current timestamp (defaults to current time).

        Returns:
            True if within budget, False if budget exceeded.
        """
        t_now = time.time() if now is None else float(now)
        cutoff = t_now - self.budget_interval_s

        if store is not None and hasattr(store, "get_boost_duration_in_window"):
            used = store.get_boost_duration_in_window(self.budget_interval_s, now=t_now)
        else:
            # Fallback to in-memory tracking
            self._in_memory_budget_history = [
                (t, dur) for t, dur in self._in_memory_budget_history if t >= cutoff
            ]
            used = sum(dur for _, dur in self._in_memory_budget_history)

        if used + duration_seconds > self.hourly_budget_seconds:
            logger.warning(
                "[safety] Rolling boost budget exhausted (used %.1fs + requested %.1fs > budget %.1fs in last %.1fs); refusing boost",
                used,
                duration_seconds,
                self.hourly_budget_seconds,
                self.budget_interval_s,
            )
            return False

        if store is None or not hasattr(store, "get_boost_duration_in_window"):
            self._in_memory_budget_history.append((t_now, duration_seconds))

        return True

    def clean_stale_cgroups(self, search_path: Optional[Path] = None) -> List[Path]:
        """Discover and remove orphaned PreCompile cgroups left from previous runs or crashes.

        Returns:
            List of removed cgroup paths.
        """
        removed: List[Path] = []
        bases = [search_path] if search_path else [Path("/sys/fs/cgroup"), self.cgroup_root]

        for base in bases:
            if not base or not base.exists():
                continue
            for p in list(base.glob("precompile-res-*")):
                if p.is_dir():
                    try:
                        # Attempt to migrate lingering procs to parent before removing
                        procs_file = p / "cgroup.procs"
                        parent_procs = base / "cgroup.procs"
                        if procs_file.exists() and parent_procs.exists():
                            try:
                                pids = procs_file.read_text().split()
                                for pid in pids:
                                    try:
                                        parent_procs.write_text(pid)
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        p.rmdir()
                        removed.append(p)
                        logger.info("[safety] Removed stale cgroup: %s", p)
                    except Exception as e:
                        logger.warning("[safety] Failed to remove stale cgroup %s: %s", p, e)
        return removed

    def is_paused(self) -> bool:
        """Check if daemon kill switch is engaged."""
        return self.pause_file.exists() or FALLBACK_PAUSE_FILE.exists()

    def pause(self) -> Path:
        """Engage kill switch by creating pause file."""
        try:
            self.pause_file.parent.mkdir(parents=True, exist_ok=True)
            self.pause_file.touch()
            return self.pause_file
        except Exception:
            FALLBACK_PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
            FALLBACK_PAUSE_FILE.touch()
            return FALLBACK_PAUSE_FILE

    def resume(self) -> bool:
        """Disengage kill switch by removing pause files."""
        removed = False
        for f in [self.pause_file, FALLBACK_PAUSE_FILE]:
            if f.exists():
                try:
                    f.unlink()
                    removed = True
                except Exception as e:
                    logger.warning("[safety] Failed to remove pause file %s: %s", f, e)
        return removed

    def record_actuation_success(self) -> None:
        """Record a successful cgroup actuation event; resets error counter."""
        self.consecutive_actuation_errors = 0

    def record_actuation_failure(self, error: Exception) -> bool:
        """Record an actuation error. Trips circuit breaker after threshold consecutive errors.

        Returns:
            True if circuit breaker tripped as a result of this failure.
        """
        self.consecutive_actuation_errors += 1
        logger.warning(
            "[safety] Actuation error (%d consecutive): %s",
            self.consecutive_actuation_errors,
            error,
        )

        if self.consecutive_actuation_errors >= self.circuit_breaker_threshold:
            self.circuit_breaker_until = self.clock() + self.circuit_breaker_cooldown
            logger.critical(
                "[safety] Circuit breaker tripped after %d consecutive actuation errors! "
                "Switching to observation-only cooldown for %.0fs (until clock=%.1f)",
                self.consecutive_actuation_errors,
                self.circuit_breaker_cooldown,
                self.circuit_breaker_until,
            )
            return True
        return False

    def is_circuit_breaker_active(self) -> bool:
        """Check if circuit breaker is currently active."""
        if self.circuit_breaker_until is not None:
            if self.clock() < self.circuit_breaker_until:
                return True
            # Cooldown elapsed -> attempt recovery
            logger.info("[safety] Circuit breaker cooldown expired. Attempting actuation recovery.")
            self.circuit_breaker_until = None
            self.consecutive_actuation_errors = 0
        return False
