"""Anticipatory reservation manager and state machine.

Stage Mapping: Stage 6 (Anticipatory Reservation), Stage 7 (Identity-Bound Claiming),
and Stage 8 (Expiry, Release, and Feedback).

State Machine:
    IDLE -> ARMED (on save + gates pass)
    ARMED -> ACTIVE (when window opens: now >= window_lo_abs)
    ARMED -> CLAIMED (early match: process appears before window opens)
    ACTIVE -> CLAIMED (process matched within window)
    ARMED / ACTIVE -> EXPIRED (window ends with no match: now > window_hi_abs)
    CLAIMED -> CLOSED (build ends, cgroup empty, or max lifetime reached)
    ANY -> CANCELLED (daemon shutdown, fatal error, project disabled)
"""

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

from precompile.cgroup import CgroupController, FakeCgroupBackend
from precompile.matcher import get_descendants
from precompile.watcher import ProcessInfo, ProcessSpawnEvent, parse_stat_content

logger = logging.getLogger("precompile.reservation")

_SWEEP_POLL_S = 0.005  # 5 ms between /proc sweeps


def _get_all_descendants_proc(
    root_pid: int,
    proc_path: Path,
    root_starttime: Optional[int] = None,
) -> Set[int]:
    """Walk /proc to find all living descendants of *root_pid*.

    Reads every numeric /proc/<N>/stat in one pass to build a parent map,
    then does a BFS from root_pid.  Only includes PIDs still alive at read time.

    Args:
        root_pid: The ancestor PID to start from.
        proc_path: Mount point of /proc (injectable for tests).
        root_starttime: If given, the root PID is confirmed only when its
            starttime_ticks still matches this value (PID-reuse guard).
            If the root is gone or reused, returns an empty set.

    Returns:
        Set of descendant PIDs (excluding root_pid itself).
    """
    # Build parent map: pid -> ppid for all live processes
    parent_map: Dict[int, int] = {}
    for entry in proc_path.iterdir():
        if not entry.name.isdigit():
            continue
        stat_file = entry / "stat"
        try:
            raw = stat_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            pid, ppid, _starttime, _comm = parse_stat_content(raw)
        except Exception:
            # Fallback: parse ppid directly from raw stat
            try:
                after_paren = raw.rsplit(")", 1)[1].split()
                pid = int(entry.name)
                ppid = int(after_paren[1])
            except Exception:
                continue
        parent_map[pid] = ppid

    # Confirm root is still the same process (PID-reuse guard)
    if root_starttime is not None:
        root_stat = proc_path / str(root_pid) / "stat"
        try:
            raw = root_stat.read_text(encoding="utf-8", errors="replace")
            _pid, _ppid, current_ticks, _comm = parse_stat_content(raw)
            if current_ticks != root_starttime:
                return set()
        except OSError:
            return set()

    # BFS to collect all descendants
    descendants: Set[int] = set()
    frontier = [root_pid]
    while frontier:
        parent = frontier.pop()
        for pid, ppid in parent_map.items():
            if ppid == parent and pid != root_pid and pid not in descendants:
                descendants.add(pid)
                frontier.append(pid)
    return descendants


def _sweep_cgroup_fixpoint(
    group_name: str,
    root_pid: int,
    root_starttime: Optional[int],
    sweep_window_s: float,
    cgroup_backend: Any,
    proc_path: Path,
    clock: Callable[[], float] = time.monotonic,
    proc_enumerator: Optional[Callable[[int, Optional[int]], Set[int]]] = None,
    stop_event: Optional[threading.Event] = None,
) -> Set[int]:
    """For *sweep_window_s* seconds, move any new descendants into the cgroup.

    Polls /proc every _SWEEP_POLL_S seconds in a fixpoint loop.  Each iteration
    discovers children that may not have existed on the previous pass.

    Args:
        group_name: Cgroup group name to move processes into.
        root_pid: The claimed process PID.
        root_starttime: Starttime ticks from /proc stat (PID-reuse guard).
        sweep_window_s: How long to keep sweeping (seconds).
        cgroup_backend: Backend providing move_pids().
        proc_path: /proc path for injectable testing.
        clock: Monotonic clock (injectable for testing).
        proc_enumerator: Optional injected callable for descendant discovery.
        stop_event: Optional threading.Event to abort sweep early.

    Returns:
        Set of all PIDs successfully moved (including root_pid).
    """
    start_time = clock()
    deadline = start_time + sweep_window_s
    already_moved: Set[int] = set()

    # Loop until deadline reached or maximum iterations elapsed.
    # Notice: In unit tests with a FakeClock, clock() does not advance during time.sleep().
    # We track elapsed real wall time as well to prevent infinite loops with fake clocks.
    wall_start = time.monotonic()
    while True:
        if stop_event is not None and stop_event.is_set():
            break

        try:
            if proc_enumerator is not None:
                descendants = proc_enumerator(root_pid, root_starttime)
            else:
                descendants = _get_all_descendants_proc(root_pid, proc_path, root_starttime)
        except Exception as e:
            logger.debug("sweep_cgroup: /proc walk error: %s", e)
            descendants = set()

        # Include root on first pass
        if not already_moved:
            new_pids = {root_pid} | descendants
        else:
            new_pids = descendants - already_moved

        if new_pids:
            try:
                cgroup_backend.move_pids(group_name, new_pids)
                already_moved.update(new_pids)
            except Exception as e:
                logger.debug("sweep_cgroup: move_pids error: %s", e)

        # Check termination: clock() advanced past deadline, or wall time exceeded sweep_window_s
        now_clock = clock()
        now_wall = time.monotonic()
        if (now_clock >= deadline) or (now_wall - wall_start >= sweep_window_s):
            break

        remaining = min(deadline - now_clock, sweep_window_s - (now_wall - wall_start))
        if remaining <= 0:
            break
        if stop_event is not None:
            if stop_event.wait(timeout=min(_SWEEP_POLL_S, remaining)):
                break
        else:
            time.sleep(min(_SWEEP_POLL_S, remaining))

    return already_moved



class ReservationState(Enum):
    """Lifecycle states of a priority reservation."""
    IDLE = auto()
    ARMED = auto()
    ACTIVE = auto()
    CLAIMED = auto()
    EXPIRED = auto()
    CLOSED = auto()
    CANCELLED = auto()


@dataclass
class ReservationRecord:
    """Represents the complete state of a priority reservation."""
    id: str
    project_id: str
    tool_profile: str
    prediction_id: Optional[int]
    t_save: float
    window_lo_abs: float
    window_hi_abs: float
    group_name: str
    state: ReservationState
    claim_flag: bool
    weight: int
    created_at: float
    claimed_pid: Optional[int] = None
    claimed_at: Optional[float] = None
    claim_latency_s: Optional[float] = None
    closed_at: Optional[float] = None
    original_cgroup: Optional[str] = None
    max_lifetime_s: float = 120.0
    reason: str = ""
    move_done: bool = False
    weight_at_claim: Optional[int] = None
    weight_at_close: Optional[int] = None
    stat_at_claim: Optional[str] = None
    stat_at_close: Optional[str] = None


class Reservation:
    """Wrapper around a ReservationRecord matching skeleton API."""

    def __init__(
        self,
        reservation_id: str,
        project_id: str,
        tool_profile: str,
        window_start_time: float,
        window_end_time: float,
        cgroup_path: Path,
        prediction_id: Optional[int] = None,
        weight: int = 500,
        max_lifetime_s: float = 120.0,
    ) -> None:
        self.record = ReservationRecord(
            id=reservation_id,
            project_id=project_id,
            tool_profile=tool_profile,
            prediction_id=prediction_id,
            t_save=window_start_time,
            window_lo_abs=window_start_time,
            window_hi_abs=window_end_time,
            group_name=cgroup_path.name,
            state=ReservationState.ARMED,
            claim_flag=False,
            weight=weight,
            created_at=time.time(),
            max_lifetime_s=max_lifetime_s,
        )
        self.cgroup_path = cgroup_path
        self._lock = threading.Lock()

    @property
    def state(self) -> ReservationState:
        return self.record.state

    def transition_to_active(self) -> None:
        with self._lock:
            if self.record.state == ReservationState.ARMED:
                self.record.state = ReservationState.ACTIVE

    def claim_atomic(self, event: Union[ProcessInfo, ProcessSpawnEvent]) -> bool:
        with self._lock:
            if self.record.state not in (ReservationState.ARMED, ReservationState.ACTIVE):
                return False
            if self.record.claim_flag:
                return False
            self.record.claim_flag = True
            self.record.state = ReservationState.CLAIMED
            self.record.claimed_pid = getattr(event, "pid", None)
            return True

    def expire(self) -> None:
        with self._lock:
            if self.record.state in (ReservationState.ARMED, ReservationState.ACTIVE):
                self.record.state = ReservationState.EXPIRED

    def close(self) -> None:
        with self._lock:
            if self.record.state == ReservationState.CLAIMED:
                self.record.state = ReservationState.CLOSED

    def cancel(self) -> None:
        with self._lock:
            self.record.state = ReservationState.CANCELLED

    def reset_timer(self, new_window_start: float, new_window_end: float) -> None:
        with self._lock:
            if self.record.state in (ReservationState.ARMED, ReservationState.ACTIVE):
                self.record.window_lo_abs = new_window_start
                self.record.window_hi_abs = new_window_end


class ReservationManager:
    """Manages active reservations across registered projects."""

    def __init__(
        self,
        cgroup_backend: Optional[Union[CgroupController, FakeCgroupBackend]] = None,
        clock: Callable[[], float] = time.monotonic,
        max_reservations: int = 4,
        atomic_claim: bool = True,
        warm_cache: bool = False,
        claim_interleave_hook: Optional[Callable[[], None]] = None,
        move_interleave_hook: Optional[Callable[[], None]] = None,
        base_cgroup_path: Path = Path("/sys/fs/cgroup"),
        proc_path: Union[str, Path] = Path("/proc"),
        sweep_window_s: float = 0.3,
        proc_enumerator: Optional[Callable[[int, Optional[int]], Set[int]]] = None,
        starttime_reader: Optional[Callable[[int], Optional[int]]] = None,
    ) -> None:
        """Initialize ReservationManager.

        Args:
            cgroup_backend: Real CgroupController or FakeCgroupBackend.
            clock: Monotonic clock provider.
            max_reservations: Maximum concurrent active reservations.
            atomic_claim: If True, uses lock-guarded test-and-set; if False, non-atomic.
            warm_cache: Cache warm-up flag (Stage 6 optional, default False).
            claim_interleave_hook: Optional callable invoked between check and set when atomic_claim=False.
            move_interleave_hook: Optional callable invoked between claim and move completion.
            base_cgroup_path: Cgroup root path.
            proc_path: Path to /proc mount.
            sweep_window_s: How long to keep sweeping /proc for new descendants after claim (seconds).
            proc_enumerator: Optional callable (root_pid, root_starttime) -> Set[int] for test injection.
            starttime_reader: Optional callable (pid) -> Optional[int] returning starttime ticks for PID-reuse check.
        """
        self.clock = clock
        self.max_reservations = max_reservations
        self.atomic_claim = atomic_claim
        self.warm_cache = warm_cache  # TODO: implement cache warm-up (Stage 6 optional)
        self.claim_interleave_hook = claim_interleave_hook
        self.move_interleave_hook = move_interleave_hook
        self.base_cgroup_path = Path(base_cgroup_path)
        self.proc_path = Path(proc_path)
        self.sweep_window_s = sweep_window_s
        self.proc_enumerator = proc_enumerator
        self.starttime_reader = starttime_reader

        if cgroup_backend is not None:
            self.cgroup_backend = cgroup_backend
            if hasattr(cgroup_backend, "base_cgroup_path"):
                self.base_cgroup_path = Path(cgroup_backend.base_cgroup_path)
        else:
            self.cgroup_backend = CgroupController(base_cgroup_path=self.base_cgroup_path)

        self._reservations: Dict[str, ReservationRecord] = {}  # res_id -> record
        self._project_res: Dict[str, str] = {}  # project_id -> res_id
        self._completed_reservations: List[ReservationRecord] = []
        self._sweep_threads: List[threading.Thread] = []
        self._sweep_stop_events: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """Check if cgroup reservation is supported and available in the current environment."""
        if isinstance(self.cgroup_backend, FakeCgroupBackend):
            return True
        try:
            # Must be root to write to cgroup v2
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                return False
            # Base cgroup directory must exist and be writable
            if not self.base_cgroup_path.exists() or not os.access(self.base_cgroup_path, os.W_OK):
                return False
            return True
        except Exception:
            return False

    def arm(
        self,
        project_id: str,
        prediction_id: Optional[int],
        tool_profile: str,
        t_save_mono: float,
        window_lo: float,
        window_hi: float,
        median_build_dur: Optional[float] = None,
        weight: int = 500,
        reason: str = "",
    ) -> Optional[ReservationRecord]:
        """Create and arm a reservation for a project, enforcing limits and timer resets.

        Args:
            project_id: Project identifier.
            prediction_id: Optional ID of triggering prediction.
            tool_profile: Tool profile name.
            t_save_mono: Monotonic timestamp of the triggering save.
            window_lo: Relative window lower bound in seconds.
            window_hi: Relative window upper bound in seconds.
            median_build_dur: Recent median build duration in seconds.
            weight: Elevated cgroup CPU weight (default 500).
            reason: Optional justification or gate reason.

        Returns:
            ReservationRecord if armed successfully, or None if unavailable/rejected.
        """
        if not self.available:
            logger.warning(
                "Cgroup reservation unavailable (requires root and writable cgroup2fs). Operating in observation-only mode."
            )
            return None

        now_mono = self.clock()
        lo_abs = t_save_mono + window_lo
        hi_abs = t_save_mono + window_hi

        with self._lock:
            # Check if project already has an active reservation
            existing_id = self._project_res.get(project_id)
            if existing_id is not None and existing_id in self._reservations:
                existing = self._reservations[existing_id]
                if existing.state in (ReservationState.ARMED, ReservationState.ACTIVE):
                    # Newer save resets its window
                    existing.t_save = t_save_mono
                    existing.window_lo_abs = lo_abs
                    existing.window_hi_abs = hi_abs
                    existing.prediction_id = prediction_id
                    existing.reason = reason or existing.reason
                    logger.debug("Reset reservation window for project %s (res %s)", project_id, existing.id)
                    return existing

            # Enforce max concurrent reservations
            active_count = sum(
                1 for r in self._reservations.values()
                if r.state in (ReservationState.ARMED, ReservationState.ACTIVE, ReservationState.CLAIMED)
            )
            if active_count >= self.max_reservations:
                logger.warning(
                    "Max concurrent reservations (%d) reached; cannot arm for project %s",
                    self.max_reservations,
                    project_id,
                )
                return None

            # Calculate max lifetime
            build_est = median_build_dur if median_build_dur is not None and median_build_dur > 0 else 1.0
            max_lifetime_s = max(5.0, min(120.0, 5.0 * build_est))

            res_id = f"res-{uuid.uuid4().hex[:8]}"
            group_name = f"precompile-{res_id}"

            # Create group in backend with elevated weight
            try:
                self.cgroup_backend.create_group(group_name, weight=weight)
            except Exception as e:
                logger.error("Failed to create cgroup for reservation %s: %s", res_id, e)
                return None

            # If window is already open at arm time, start in ACTIVE
            initial_state = ReservationState.ACTIVE if now_mono >= lo_abs else ReservationState.ARMED

            rec = ReservationRecord(
                id=res_id,
                project_id=project_id,
                tool_profile=tool_profile,
                prediction_id=prediction_id,
                t_save=t_save_mono,
                window_lo_abs=lo_abs,
                window_hi_abs=hi_abs,
                group_name=group_name,
                state=initial_state,
                claim_flag=False,
                weight=weight,
                created_at=time.time(),
                max_lifetime_s=max_lifetime_s,
                reason=reason,
            )

            self._reservations[res_id] = rec
            self._project_res[project_id] = res_id
            logger.info("Armed reservation %s for project %s (state=%s, weight=%d)", res_id, project_id, initial_state.name, weight)
            return rec

    def try_claim(
        self,
        project_id: str,
        proc: ProcessInfo,
        process_table: Optional[Dict[int, Any]] = None,
    ) -> bool:
        """Attempt to claim an active reservation for a matching process.

        Permitted from ARMED (early match) or ACTIVE state.
        Winner is migrated to cgroup together with descendants.

        Args:
            project_id: Project identifier.
            proc: ProcessInfo of candidate.
            process_table: Optional mapping of PID -> ProcessInfo/PPID for descendant resolution.

        Returns:
            True if claim succeeded and process was migrated, False otherwise.
        """
        now_mono = self.clock()

        with self._lock:
            res_id = self._project_res.get(project_id)
            if res_id is None or res_id not in self._reservations:
                return False

            res = self._reservations[res_id]
            if res.state not in (ReservationState.ARMED, ReservationState.ACTIVE):
                return False

            # Check if this process was already claimed by any reservation
            for other_res in self._reservations.values():
                if other_res.claimed_pid == proc.pid:
                    return False

            # Claim concurrency control
            if self.atomic_claim:
                if res.claim_flag:
                    return False
                res.claim_flag = True
                res.state = ReservationState.CLAIMED
                res.claimed_pid = proc.pid
                res.claimed_at = now_mono
                res.claim_latency_s = max(0.0, now_mono - proc.t_start_monotonic)
                res.original_cgroup = self.cgroup_backend.original_cgroup_of(proc.pid)

        if not self.atomic_claim:
            # Deliberately non-atomic check-then-set outside the lock for ablation studies
            if res.claim_flag:
                return False
            if self.claim_interleave_hook is not None:
                try:
                    self.claim_interleave_hook()
                except Exception:
                    pass
            res.claim_flag = True
            with self._lock:
                res.state = ReservationState.CLAIMED
                res.claimed_pid = proc.pid
                res.claimed_at = now_mono
                res.claim_latency_s = max(0.0, now_mono - proc.t_start_monotonic)
                res.original_cgroup = self.cgroup_backend.original_cgroup_of(proc.pid)

        # Test hook right after claiming but before process migration
        if self.move_interleave_hook is not None:
            try:
                self.move_interleave_hook()
            except Exception:
                pass

        # Re-check PID starttime in /proc still equals proc.starttime_ticks (PID reuse guard)
        if self.starttime_reader is not None:
            current_ticks = self.starttime_reader(proc.pid)
            if current_ticks is not None and current_ticks != proc.starttime_ticks:
                logger.warning(
                    "PID reuse detected for PID %d: recorded ticks=%d != current ticks=%d; refusing move",
                    proc.pid,
                    proc.starttime_ticks,
                    current_ticks,
                )
                return False
        else:
            stat_file = self.proc_path / str(proc.pid) / "stat"
            if stat_file.exists():
                try:
                    stat_content = stat_file.read_text(encoding="utf-8")
                    _, _, current_ticks, _ = parse_stat_content(stat_content)
                    if current_ticks != proc.starttime_ticks:
                        logger.warning(
                            "PID reuse detected for PID %d: recorded ticks=%d != current ticks=%d; refusing move",
                            proc.pid,
                            proc.starttime_ticks,
                            current_ticks,
                        )
                        return False
                except Exception as e:
                    logger.warning("Could not re-verify PID %d in /proc: %s; aborting claim", proc.pid, e)
                    return False

        # Move winner and existing descendants from process_table if provided
        pids_to_move = {proc.pid}
        if process_table is not None:
            descendants = get_descendants(proc.pid, process_table)
            pids_to_move.update(descendants)

        try:
            self.cgroup_backend.move_pids(res.group_name, pids_to_move)
        except Exception as e:
            logger.warning("Error moving processes into cgroup %s: %s", res.group_name, e)

        # Record cpu.weight and cpu.stat at claim time
        try:
            w_file = self.base_cgroup_path / res.group_name / "cpu.weight"
            if w_file.exists():
                res.weight_at_claim = int(w_file.read_text(encoding="utf-8").strip())
            s_file = self.base_cgroup_path / res.group_name / "cpu.stat"
            if s_file.exists():
                res.stat_at_claim = s_file.read_text(encoding="utf-8").strip()
            logger.info(
                "[cgroup_weight] res=%s group=%s weight_at_claim=%s",
                res.id,
                res.group_name,
                res.weight_at_claim,
            )
        except Exception as e:
            logger.debug("Failed to read weight/stat at claim for %s: %s", res.group_name, e)

        # Fixpoint sweep: walk /proc every 5ms for sweep_window_s seconds to
        # move late-forked children (such as cc1) into the reservation cgroup.
        # Run asynchronously in a background thread so try_claim returns immediately (<10ms).
        # tick() is guarded by res.move_done and will not close the group before the sweep completes.
        should_sweep = (
            self.sweep_window_s > 0
            and (not isinstance(self.cgroup_backend, FakeCgroupBackend) or self.proc_enumerator is not None)
        )

        if should_sweep:
            stop_ev = threading.Event()
            self._sweep_stop_events[res.id] = stop_ev

            def _background_sweep() -> None:
                try:
                    _sweep_cgroup_fixpoint(
                        group_name=res.group_name,
                        root_pid=proc.pid,
                        root_starttime=proc.starttime_ticks,
                        sweep_window_s=self.sweep_window_s,
                        cgroup_backend=self.cgroup_backend,
                        proc_path=self.proc_path,
                        clock=self.clock,
                        proc_enumerator=self.proc_enumerator,
                        stop_event=stop_ev,
                    )
                except Exception as e:
                    logger.debug("Background fixpoint sweep exception: %s", e)
                finally:
                    res.move_done = True
                    self._sweep_stop_events.pop(res.id, None)

            sweep_thread = threading.Thread(
                target=_background_sweep,
                name=f"sweep-{res.id}",
                daemon=True,
            )
            self._sweep_threads.append(sweep_thread)
            sweep_thread.start()
        else:
            # Sweeper disabled or FakeCgroupBackend without enumerator: move is immediately done
            res.move_done = True

        logger.info(
            "Claimed reservation %s for PID %d (latency=%.3fs, initial_moved=%d pids)",
            res.id,
            proc.pid,
            res.claim_latency_s,
            len(pids_to_move),
        )
        return True

    def tick(self, now: Optional[float] = None) -> List[ReservationRecord]:
        """Advance time-driven state transitions for all managed reservations.

        Transitions:
            ARMED -> ACTIVE (window opens)
            ARMED / ACTIVE -> EXPIRED (window closes without claim)
            CLAIMED -> CLOSED (cgroup empty or max lifetime exceeded)

        Args:
            now: Optional current monotonic timestamp.

        Returns:
            List of reservations that transitioned in this tick.
        """
        now_mono = now if now is not None else self.clock()
        transitioned: List[ReservationRecord] = []

        with self._lock:
            for res_id, res in list(self._reservations.items()):
                # 1. ARMED -> ACTIVE when window opens
                if res.state == ReservationState.ARMED:
                    if now_mono >= res.window_lo_abs:
                        res.state = ReservationState.ACTIVE
                        transitioned.append(res)
                        logger.debug("Reservation %s window opened -> ACTIVE", res.id)

                # 2. ARMED / ACTIVE -> EXPIRED when window closes unclaimed
                if res.state in (ReservationState.ARMED, ReservationState.ACTIVE):
                    if now_mono > res.window_hi_abs and not res.claim_flag:
                        res.state = ReservationState.EXPIRED
                        res.closed_at = now_mono
                        transitioned.append(res)
                        self._release_reservation(res)
                        logger.info("Reservation %s expired unclaimed -> EXPIRED", res.id)

                # 3. CLAIMED -> CLOSED when empty or max lifetime exceeded
                elif res.state == ReservationState.CLAIMED:
                    # Guard against claim/tick race: never close for "empty group" until move_done
                    if not res.move_done:
                        continue

                    is_empty = not self.cgroup_backend.is_populated(res.group_name)
                    lifetime_exceeded = (
                        res.claimed_at is not None and (now_mono - res.claimed_at >= res.max_lifetime_s)
                    )

                    if lifetime_exceeded and not is_empty:
                        # Revert elevated weight to baseline 100 before closing
                        try:
                            self.cgroup_backend.set_weight(res.group_name, 100)
                        except Exception:
                            pass
                        logger.info("Reservation %s exceeded max lifetime (%.1fs); reverted weight to 100", res.id, res.max_lifetime_s)

                    if is_empty or lifetime_exceeded:
                        res.state = ReservationState.CLOSED
                        res.closed_at = now_mono
                        transitioned.append(res)
                        self._release_reservation(res)
                        logger.info("Reservation %s finished -> CLOSED (empty=%s, lifetime_exceeded=%s)", res.id, is_empty, lifetime_exceeded)

        return transitioned

    def cancel(self, reservation_id: str, reason: str = "") -> bool:
        """Cancel an active reservation and clean up its cgroup.

        Args:
            reservation_id: Target reservation ID.
            reason: Cancellation reason.

        Returns:
            True if cancelled, False if not found or already terminal.
        """
        with self._lock:
            res = self._reservations.get(reservation_id)
            if res is None:
                return False
            if res.state in (ReservationState.CLOSED, ReservationState.EXPIRED, ReservationState.CANCELLED):
                return False

            res.state = ReservationState.CANCELLED
            res.closed_at = self.clock()
            res.reason = reason or res.reason
            self._release_reservation(res)
            logger.info("Cancelled reservation %s (reason: %s)", reservation_id, reason)
            return True

    def cancel_all(self, reason: str = "shutdown") -> None:
        """Cancel all managed reservations."""
        with self._lock:
            for res_id in list(self._reservations.keys()):
                res = self._reservations[res_id]
                if res.state not in (ReservationState.CLOSED, ReservationState.EXPIRED, ReservationState.CANCELLED):
                    res.state = ReservationState.CANCELLED
                    res.closed_at = self.clock()
                    res.reason = reason
                    self._release_reservation(res)

    def _release_reservation(self, res: ReservationRecord) -> None:
        """Internal helper to clean up cgroup and bookkeeping for a terminal reservation."""
        # Stop background sweeper if still running
        stop_ev = self._sweep_stop_events.pop(res.id, None)
        if stop_ev is not None:
            stop_ev.set()
        # Read group's cpu.stat usage_usec and compute cpu_share before releasing
        try:
            w_file = self.base_cgroup_path / res.group_name / "cpu.weight"
            if w_file.exists():
                res.weight_at_close = int(w_file.read_text(encoding="utf-8").strip())
            stat_file = self.base_cgroup_path / res.group_name / "cpu.stat"
            if stat_file.exists():
                content = stat_file.read_text(encoding="utf-8")
                res.stat_at_close = content
                usage_usec = 0
                for line in content.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[0] == "usage_usec":
                        usage_usec = int(parts[1])
                        break
                wall_time_s = max(0.001, (res.closed_at or self.clock()) - (res.claimed_at or res.t_save))
                cpu_share = (usage_usec / 1e6) / wall_time_s
                logger.info(
                    "[cgroup_cpu] res=%s group=%s usage_usec=%d wall=%.3fs cpu_share=%.2f (%.1f%%) weight_claim=%s weight_close=%s stat=%s",
                    res.id,
                    res.group_name,
                    usage_usec,
                    wall_time_s,
                    cpu_share,
                    cpu_share * 100.0,
                    getattr(res, "weight_at_claim", None),
                    getattr(res, "weight_at_close", None),
                    content.replace("\n", " "),
                )
        except Exception as e:
            logger.debug("Failed to read cpu.stat for %s: %s", res.group_name, e)

        try:
            self.cgroup_backend.release_group(res.group_name, fallback_target=res.original_cgroup)
        except Exception as e:
            logger.warning("Error releasing cgroup %s: %s", res.group_name, e)

        # Clear project mapping
        if self._project_res.get(res.project_id) == res.id:
            del self._project_res[res.project_id]

        self._completed_reservations.append(res)
        if len(self._completed_reservations) > 50:
            self._completed_reservations = self._completed_reservations[-50:]

    def get_reservation(self, reservation_id: str) -> Optional[ReservationRecord]:
        """Fetch reservation by ID."""
        with self._lock:
            if reservation_id in self._reservations:
                return self._reservations[reservation_id]
            for r in self._completed_reservations:
                if r.id == reservation_id:
                    return r
            return None

    def get_reservation_for_project(self, project_id: str) -> Optional[ReservationRecord]:
        """Fetch active reservation record for a project, if one exists."""
        with self._lock:
            res_id = self._project_res.get(project_id)
            if res_id is not None:
                return self._reservations.get(res_id)
            return None

    def list_active(self) -> List[ReservationRecord]:
        """List all currently active (ARMED, ACTIVE, CLAIMED) reservations."""
        with self._lock:
            return [
                r for r in self._reservations.values()
                if r.state in (ReservationState.ARMED, ReservationState.ACTIVE, ReservationState.CLAIMED)
            ]

    def list_all(self) -> List[ReservationRecord]:
        """List all reservations (active and completed)."""
        with self._lock:
            return list(self._reservations.values()) + list(self._completed_reservations)
