"""Daemon orchestrator running PreCompile in observation and actuation modes.

Stage Mapping: Stages 1–6 (Observation Pass), Stage 5 (Contention & Cost-Benefit Gates),
and Stages 6–8 (Anticipatory Priority Reservation & Claiming).

Wires DataStore, FileMonitor, ProcessWatcher, IdentityMatcher, LatencyPredictor,
ArmingGate, ReservationManager, and ProcessExitMonitor.

Supports:
- Observation-only mode (default, zero actuation).
- Actuation mode (`--actuate`, requires root & cgroup v2): arms reservations on 'would_arm',
  claims matching build processes, migrates them to elevated cgroups, and records
  accurate exit durations via pidfd_open.
- SIGHUP reload: reloads project list dynamically without losing state.
"""

import collections
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from precompile.cgroup import CgroupController, FakeCgroupBackend
from precompile.config import AppConfig, PredictorConfig
from precompile.exit_monitor import ProcessExitMonitor
from precompile.gates import ArmingGate, read_psi_cpu
from precompile.matcher import IdentityMatcher, ToolProfile, match, resolve_tool_profile, resolve_topmost_ancestor
from precompile.monitor import FileMonitor, SaveEvent
from precompile.predictor import LatencyPredictor, Prediction
from precompile.reservation import ReservationManager, ReservationRecord, ReservationState
from precompile.store import DataStore
from precompile.watcher import ProcessInfo, ProcessWatcher, parse_stat_content

logger = logging.getLogger("precompile.daemon")


@dataclass
class _TrackedBuild:
    """Internal state for an ongoing build."""
    pid: int
    project_id: str
    t_start_wall: float
    t_start_mono: float
    contention_level: float
    prediction_id: Optional[int]
    proc_name: str = ""
    ppid: Optional[int] = None
    cmdline_0: str = ""
    condition: str = "observed"  # 'reserved', 'holdout', or 'observed'
    reservation_id: Optional[str] = None


class PreCompileDaemon:
    """Orchestrator for PreCompile with observation and actuation capabilities."""

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        db_path: Optional[Union[str, Path]] = None,
        correlation_window_s: Optional[float] = None,
        pid_file: Optional[Union[str, Path]] = None,
        proc_path: Union[str, Path] = "/proc",
        clock: Callable[[], float] = time.monotonic,
        actuate: bool = False,
        holdout_probability: Optional[float] = None,
        reservation_weight: Optional[int] = None,
        sweep_window_s: Optional[float] = None,
        # Stage 9 Benefit Estimator parameters
        eval_every: Optional[int] = None,
        min_total_n: Optional[int] = None,
        min_bin_n: Optional[int] = None,
        contention_gate_enabled: bool = True,
        cost_gate_enabled: bool = True,
        leak_check: bool = False,
        # Stage 10 Safety, Conditions & Ablations
        condition: str = "c2-full",
        ablation_no_confidence_gate: bool = False,
        ablation_no_contention_gate: bool = False,
        ablation_fixed_window: Optional[Tuple[float, float]] = None,
        ablation_no_holdout: bool = False,
        ablation_no_atomic_claim: bool = False,
        safety_governor: Optional[Any] = None,
        boost_budget_seconds: Optional[float] = None,
        budget_interval_s: Optional[float] = None,
        max_cgroup_weight: Optional[int] = None,
        # Injectable components for testing
        arming_gate: Optional[ArmingGate] = None,
        reservation_manager: Optional[ReservationManager] = None,
        cgroup_backend: Optional[Union[CgroupController, FakeCgroupBackend]] = None,
        benefit_estimator: Optional[Any] = None,
    ) -> None:
        """Initialize PreCompileDaemon.

        Args:
            config: Optional AppConfig instance.
            db_path: Path to SQLite database file or ':memory:'.
            correlation_window_s: Optional override for correlation window duration.
            pid_file: Optional path to write process PID file upon start.
            proc_path: Proc filesystem mount point (default: '/proc').
            clock: Monotonic clock provider.
            actuate: If True, enables priority reservation actuation via cgroups (requires root).
            holdout_probability: Override for holdout probability.
            reservation_weight: Override for cgroup CPU weight.
            sweep_window_s: Override for descendant fixpoint sweep window in seconds (0 to disable).
            eval_every: Override for benefit evaluation interval (number of builds).
            min_total_n: Override for minimum builds per arm before evaluating benefit.
            min_bin_n: Override for minimum builds per arm in a bin before evaluating bin effect.
            contention_gate_enabled: If False, disables contention gate check.
            cost_gate_enabled: If False, disables cost/benefit gate check.
            leak_check: If True, enables background leak-checker thread instrumentation.
            condition: Baseline experimental condition ('c1-reactive', 'c2-full', 'c3-static').
            ablation_no_confidence_gate: Ablation A2: arms even if can_predict=False.
            ablation_no_contention_gate: Ablation A3: arms regardless of system PSI contention.
            ablation_fixed_window: Ablation A4: uses fixed (lo, hi) window instead of predicted EWMA.
            ablation_no_holdout: Ablation A5: disables holdouts and benefit controller feedback.
            safety_governor: Optional injectable SafetyGovernor.
            boost_budget_seconds: Rolling boost budget in seconds (default: 900s).
            budget_interval_s: Rolling budget interval in seconds (default: 3600s).
            max_cgroup_weight: Maximum cgroup weight ceiling (clamped <= 1000).
            arming_gate: Optional injectable ArmingGate.
            reservation_manager: Optional injectable ReservationManager.
            cgroup_backend: Optional injectable cgroup backend.
            benefit_estimator: Optional injectable BenefitEstimator.
        """
        # Validate condition and ablation combinations
        if condition == "c1-reactive":
            if ablation_no_confidence_gate or ablation_fixed_window is not None or ablation_no_holdout:
                raise ValueError("Ablations A2, A4, A5 cannot be combined with --condition c1-reactive.")
        elif condition == "c3-static":
            if (
                ablation_no_confidence_gate
                or ablation_no_contention_gate
                or ablation_fixed_window is not None
                or ablation_no_holdout
            ):
                raise ValueError("Ablations cannot be combined with --condition c3-static.")

        self.condition = condition
        self.ablation_no_confidence_gate = ablation_no_confidence_gate
        self.ablation_no_contention_gate = ablation_no_contention_gate
        self.ablation_fixed_window = ablation_fixed_window
        self.ablation_no_holdout = ablation_no_holdout
        self.ablation_no_atomic_claim = ablation_no_atomic_claim

        self.config = config if config is not None else AppConfig()
        self.pid_file = Path(pid_file) if pid_file is not None else None
        self.proc_path = Path(proc_path)
        self.clock = clock

        if ablation_no_contention_gate or condition == "c3-static":
            self.contention_gate_enabled = False
        else:
            self.contention_gate_enabled = contention_gate_enabled

        if condition == "c3-static":
            self.cost_gate_enabled = False
        else:
            self.cost_gate_enabled = cost_gate_enabled

        self.leak_check = leak_check

        # Store initialization
        if db_path is not None:
            self.store = DataStore(db_path=db_path, config=self.config.store)
        else:
            self.store = DataStore(config=self.config.store)

        # Predictor configuration
        pred_cfg = PredictorConfig(
            alpha=self.config.predictor.alpha,
            k=self.config.predictor.k,
            margin_m=self.config.predictor.margin_m,
            clip_sigma=self.config.predictor.clip_sigma,
            clip_min_samples=self.config.predictor.clip_min_samples,
            clip_sigma_floor_s=self.config.predictor.clip_sigma_floor_s,
            correlation_window_s=(
                correlation_window_s
                if correlation_window_s is not None
                else self.config.predictor.correlation_window_s
            ),
            min_samples=self.config.predictor.min_samples,
            cv_max=self.config.predictor.cv_max,
            sigma_max_small_s=self.config.predictor.sigma_max_small_s,
            hit_rate_min=self.config.predictor.hit_rate_min,
            hit_rate_last=self.config.predictor.hit_rate_last,
            min_predictions=self.config.predictor.min_predictions,
            strict_min_predictions_to_arm=self.config.predictor.strict_min_predictions_to_arm,
            learned_file_classes=self.config.predictor.learned_file_classes,
            negative_delay_tolerance_s=self.config.predictor.negative_delay_tolerance_s,
        )

        self.predictor = LatencyPredictor(
            store=self.store,
            config=pred_cfg,
            clock=self.clock,
        )

        self.matcher = IdentityMatcher()

        # Monitor and Watcher
        self.monitor = FileMonitor(
            config=self.config.monitor,
            on_save=self._on_save_event,
        )

        self.watcher = ProcessWatcher(
            poll_interval_normal_ms=self.config.watcher.poll_interval_normal_ms,
            poll_interval_intensified_ms=self.config.watcher.poll_interval_intensified_ms,
            on_batch=self._on_process_batch,
            proc_path=self.proc_path,
            grace_period_seconds=self.config.watcher.grace_period_seconds,
            max_table_size=self.config.watcher.max_table_size,
        )

        # Actuation settings
        requested_actuate = actuate or getattr(self.config, "actuate", False)
        configured_weight = (
            reservation_weight
            if reservation_weight is not None
            else getattr(self.config, "reservation_weight", 500)
        )
        h_prob = (
            0.0
            if (self.ablation_no_holdout or self.condition in ("c1-reactive", "c3-static"))
            else (
                holdout_probability
                if holdout_probability is not None
                else getattr(self.config, "holdout_probability", 0.1)
            )
        )

        # Safety Governor setup (Stage 10)
        from precompile.safety import SafetyGovernor
        max_w = (
            max_cgroup_weight
            if max_cgroup_weight is not None
            else getattr(self.config.safety, "max_cgroup_weight", 1000)
        )
        max_boost_s = getattr(self.config.safety, "max_boost_duration_seconds", 120.0)
        hourly_budget = (
            boost_budget_seconds
            if boost_budget_seconds is not None
            else getattr(self.config.safety, "budget_max_elevated_seconds_per_hour", 900.0)
        )
        budget_win = (
            budget_interval_s
            if budget_interval_s is not None
            else getattr(self.config.safety, "budget_interval_s", 3600.0)
        )

        if safety_governor is not None:
            self.safety_governor = safety_governor
        else:
            self.safety_governor = SafetyGovernor(
                max_weight=max_w,
                max_boost_seconds=max_boost_s,
                hourly_budget_seconds=hourly_budget,
                budget_interval_s=budget_win,
                clock=self.clock,
            )

        self.reservation_weight = self.safety_governor.clamp_weight(configured_weight)

        # ArmingGate
        if arming_gate is not None:
            self._arming_gate = arming_gate
        else:
            self._arming_gate = ArmingGate(
                holdout_probability=h_prob,
                contention_gate_enabled=self.contention_gate_enabled,
                cost_gate_enabled=self.cost_gate_enabled,
                clock=self.clock,
            )
        self._last_psi_sample_mono: float = 0.0

        # BenefitEstimator setup (Stage 9)
        from precompile.benefit import BenefitEstimator
        if benefit_estimator is not None:
            self.benefit_estimator = benefit_estimator
        elif self.condition in ("c1-reactive", "c3-static") or self.ablation_no_holdout:
            self.benefit_estimator = None
        else:
            b_eval_every = eval_every if eval_every is not None else 10
            b_min_total_n = min_total_n if min_total_n is not None else 8
            b_min_bin_n = min_bin_n if min_bin_n is not None else 5
            self.benefit_estimator = BenefitEstimator(
                store=self.store,
                configured_weight=self.reservation_weight,
                min_total_n=b_min_total_n,
                min_bin_n=b_min_bin_n,
                eval_every=b_eval_every,
                clock=self.clock,
                contention_gate_enabled=self.contention_gate_enabled,
            )

        # ReservationManager setup
        sweep_win = (
            sweep_window_s
            if sweep_window_s is not None
            else getattr(self.config.watcher, "sweep_window_s", 0.3)
        )
        if reservation_manager is not None:
            self.reservation_manager = reservation_manager
            self._actuate_requested = requested_actuate
        else:
            backend = cgroup_backend if cgroup_backend is not None else CgroupController()
            self.reservation_manager = ReservationManager(
                cgroup_backend=backend,
                clock=self.clock,
                proc_path=self.proc_path,
                sweep_window_s=sweep_win,
                atomic_claim=not ablation_no_atomic_claim,
            )
            self._actuate_requested = requested_actuate

        # Fallback check: if actuate requested, check root privilege and cgroup writable status
        if self._actuate_requested:
            can_priv = self.safety_governor.check_privileges(self.reservation_manager.base_cgroup_path)
            if not can_priv or not self.reservation_manager.available:
                reason = self.safety_governor.last_degradation_reason or "backend_unavailable"
                logger.warning(
                    "Priority reservation actuation requested (--actuate), but degraded (%s). "
                    "Continuing in observation-only mode.",
                    reason,
                )
                self.actuate = False
            else:
                self.actuate = True
        else:
            self.actuate = False

        # Exit monitor via pidfd_open for millisecond-accurate exit detection
        self._exit_monitor = ProcessExitMonitor(on_exit=self._on_process_exit)

        # Ordering robustness buffer: project_id -> list of (t_start_mono, ProcessInfo)
        self._spawn_buffer: Dict[str, List[Tuple[float, ProcessInfo]]] = collections.defaultdict(list)

        # Tracking active builds: pid -> _TrackedBuild
        self._tracked_builds: Dict[int, _TrackedBuild] = {}
        # Unsuppression tail deadlines: project_id -> deadline_monotonic
        self._unsuppress_at: Dict[str, float] = {}

        # Track which project IDs are currently watched to support SIGHUP reload
        self._watched_project_ids: Set[str] = set()

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._housekeeping_thread: Optional[threading.Thread] = None
        self._running = False

    def _on_save_event(self, save_event: SaveEvent) -> None:
        """Callback invoked by FileMonitor upon a debounced save event."""
        with self._lock:
            # Under C1-reactive or C3-static, do not track or predict saves in advance
            if self.condition in ("c1-reactive", "c3-static"):
                return

            # Ignore file classes not in learned_file_classes (e.g. temporary editor files)
            if save_event.file_class not in self.predictor.config.learned_file_classes:
                return

            try:
                rel_path = save_event.path.name
            except Exception:
                rel_path = str(save_event.path)

            logger.info(
                f"[save]    {save_event.project_id} {rel_path} ({save_event.file_class}) burst={save_event.burst_size}"
            )

            pred = self.predictor.on_save(save_event)
            if pred is None and (self.ablation_no_confidence_gate or self.ablation_fixed_window is not None):
                # A2 or A4 forces prediction even when confidence gate would reject
                w_lo = 0.0
                w_hi = 5.0
                if self.ablation_fixed_window is not None:
                    w_lo, w_hi = self.ablation_fixed_window

                proj = self.store.get_project(save_event.project_id)
                tool_profile = proj.tool_profile_name if proj is not None else "default"
                key = (save_event.project_id, save_event.file_class, tool_profile)
                pred_id = self.store.record_prediction(
                    project_id=save_event.project_id,
                    file_class=save_event.file_class,
                    tool_profile=tool_profile,
                    t_save_wall=save_event.t_save_wall,
                    window_lo=w_lo,
                    window_hi=w_hi,
                    armed=False,
                    holdout=False,
                )
                from precompile.gates import GateResult
                gate_res = GateResult(can_predict=True, can_arm=True, reasons=["ablation_override"])
                pred = Prediction(
                    prediction_id=pred_id,
                    key=key,
                    t_save_monotonic=save_event.t_save_monotonic,
                    window_lo=w_lo,
                    window_hi=w_hi,
                    can_arm=True,
                    gate_result=gate_res,
                )
                with self.predictor._lock:
                    if save_event.project_id in self.predictor._pending_saves:
                        self.predictor._pending_saves[save_event.project_id].prediction_id = pred_id
                        self.predictor._pending_saves[save_event.project_id].window_lo = w_lo
                        self.predictor._pending_saves[save_event.project_id].window_hi = w_hi

            if pred is not None:
                # Apply Ablation A2: no confidence gate (always can_predict and can_arm)
                if self.ablation_no_confidence_gate:
                    pred.gate_result.can_predict = True
                    pred.can_arm = True
                    if pred.window_lo is None or pred.window_hi is None or (pred.window_hi <= pred.window_lo):
                        pred.window_lo = 0.0
                        pred.window_hi = 5.0
                    with self.predictor._lock:
                        if save_event.project_id in self.predictor._pending_saves:
                            self.predictor._pending_saves[save_event.project_id].window_lo = pred.window_lo
                            self.predictor._pending_saves[save_event.project_id].window_hi = pred.window_hi

                # Apply Ablation A4: fixed window override
                if self.ablation_fixed_window is not None:
                    pred.window_lo, pred.window_hi = self.ablation_fixed_window
                    pred.gate_result.can_predict = True
                    pred.can_arm = True
                    with self.predictor._lock:
                        if save_event.project_id in self.predictor._pending_saves:
                            self.predictor._pending_saves[save_event.project_id].window_lo = pred.window_lo
                            self.predictor._pending_saves[save_event.project_id].window_hi = pred.window_hi

                reasons_str = f" ({'; '.join(pred.gate_result.reasons)})" if pred.gate_result.reasons else ""
                logger.info(
                    f"[predict] {save_event.project_id} window=[{pred.window_lo:.2f}, {pred.window_hi:.2f}]s "
                    f"can_predict={pred.gate_result.can_predict} can_arm={pred.can_arm}{reasons_str}"
                )

                # Evaluate ArmingGate and arm reservation if actuating
                self._evaluate_arming_gate(save_event, pred)

            # Ordering Robustness:
            # Check spawn buffer for any matched spawn whose t_start_monotonic >= t_save_monotonic - tolerance
            proj_id = save_event.project_id
            buffered = self._spawn_buffer.get(proj_id, [])
            neg_tol = self.predictor.config.negative_delay_tolerance_s
            eligible = [s for s in buffered if s[0] >= save_event.t_save_monotonic - neg_tol]
            if eligible:
                # Pick the earliest spawn occurring after or at the save time
                eligible.sort(key=lambda s: s[0])
                chosen_t_start, chosen_proc = eligible[0]
                self._spawn_buffer[proj_id].remove((chosen_t_start, chosen_proc))
                self._handle_matched_spawn(proj_id, chosen_proc, chosen_t_start)

    def _evaluate_arming_gate(self, save_event: SaveEvent, pred: "Prediction") -> None:
        """Evaluate the arming gate after a prediction and arm reservation if actuating."""
        proj_id = save_event.project_id
        pred_id = pred.prediction_id
        if pred_id is None:
            return

        # Gather inputs
        p_hit = getattr(pred, "recent_hit_rate", 0.0)
        if p_hit == 0.0:
            try:
                tool_profile = pred.key[2] if hasattr(pred, "key") and len(pred.key) >= 3 else ""
                if not tool_profile or tool_profile == "default":
                    proj = self.store.get_project(proj_id)
                    if proj and proj.tool_profile_name:
                        tool_profile = proj.tool_profile_name
                _, _, p_hit = self.store.recent_hit_rate(
                    proj_id,
                    save_event.file_class,
                    tool_profile if tool_profile else "default",
                )
            except Exception:
                p_hit = 0.0

        # Get recent observed build durations
        try:
            build_durations = self.store.recent_build_durations(proj_id, "observed", n=10)
        except Exception:
            build_durations = []

        window_lo = pred.window_lo if pred.window_lo is not None else 0.0
        window_hi = pred.window_hi if pred.window_hi is not None else 0.0

        # Query measured speedup fraction from BenefitEstimator if available
        # We sample PSI briefly to estimate current contention level for the lookup
        psi_metrics = self._arming_gate._psi_sampler.sample()
        cur_cl = self._arming_gate.compute_contention_level(
            psi_metrics, self._arming_gate._psi_sampler.recent_some_pct
        )
        measured_speedup = (
            self.benefit_estimator.expected_speedup_frac(proj_id, cur_cl)
            if self.benefit_estimator is not None
            else None
        )

        # Check if project arming is disabled by BenefitEstimator
        is_probe = False
        if self.benefit_estimator is not None and not self.ablation_no_holdout:
            arming_enabled_for_proj = self.benefit_estimator.is_arming_enabled(proj_id)
            if not arming_enabled_for_proj:
                # Check if this save should be selected as an exploratory probe
                if self.benefit_estimator.should_probe(proj_id):
                    is_probe = True
                    pred_can_arm = True
                    logger.info(f"[benefit] {proj_id} issuing exploratory probe reservation")
                else:
                    pred_can_arm = False
            else:
                pred_can_arm = pred.can_arm
        else:
            pred_can_arm = pred.can_arm

        if self.ablation_no_confidence_gate:
            pred_can_arm = True

        arm_decision = self._arming_gate.decide(
            can_arm=pred_can_arm or is_probe,
            p_hit=p_hit,
            build_durations=build_durations,
            window_lo=window_lo,
            window_hi=window_hi,
            gate_reasons=list(pred.gate_result.reasons),
            speedup_frac=measured_speedup,
        )

        d = arm_decision.decision
        cl = arm_decision.contention_level
        ph = arm_decision.p_hit
        bd = arm_decision.build_dur
        bn = arm_decision.benefit_s
        cs = arm_decision.cost_s

        if is_probe and d == "would_arm":
            d = "probe"

        # Under Ablation A5 (no holdout), override holdout decision to arming
        if self.ablation_no_holdout and d == "holdout":
            d = "would_arm"
            arm_decision.holdout = False

        # Safety Governor checks before arming
        if d in ("would_arm", "probe"):
            win_dur = max(1.0, window_hi - window_lo)
            if self.safety_governor.is_paused():
                logger.info(f"[arm?]    {proj_id} abstain: kill switch active (paused)")
                d = "abstain_paused"
            elif self.safety_governor.is_circuit_breaker_active():
                logger.info(f"[arm?]    {proj_id} abstain: circuit breaker active")
                d = "abstain_circuit_breaker"
            elif not self.safety_governor.check_and_consume_budget(win_dur, store=self.store):
                logger.info(f"[arm?]    {proj_id} abstain: boost budget exhausted")
                d = "abstain_budget"

        # Determine dynamic reservation weight for this project
        if self.benefit_estimator is not None and not is_probe:
            proj_weight = self.benefit_estimator.current_weight(proj_id)
        else:
            proj_weight = self.reservation_weight

        if d in ("would_arm", "probe"):
            if self.actuate and self.reservation_manager.available:
                # Arm reservation
                tool_prof = getattr(save_event, "tool_profile", "default")
                try:
                    res = self.reservation_manager.arm(
                        project_id=proj_id,
                        prediction_id=pred_id,
                        tool_profile=tool_prof,
                        t_save_mono=save_event.t_save_monotonic,
                        window_lo=window_lo,
                        window_hi=window_hi,
                        median_build_dur=bd,
                        weight=proj_weight,
                        reason=f"psi={cl:.1f} benefit={bn:.2f}s cost={cs:.2f}s probe={is_probe}",
                    )
                    if res is not None:
                        self.safety_governor.record_actuation_success()
                        d = "armed" if not is_probe else "probe_armed"
                        try:
                            self.store.set_prediction_armed(pred_id, True)
                        except Exception as e:
                            logger.warning("Failed to set prediction armed: %s", e)
                        logger.info(
                            f"[arm]    {proj_id} ARMED res={res.id} window=[{window_lo:.2f}, {window_hi:.2f}]s "
                            f"weight={res.weight} (probe={is_probe})"
                        )
                    else:
                        logger.info(
                            f"[arm?]    {proj_id} would ARM "
                            f"(psi={cl:.1f} p_hit={ph:.2f} dur={bd:.2f}s benefit={bn:.2f}s cost={cs:.2f}s)"
                        )
                except Exception as e:
                    self.safety_governor.record_actuation_failure(e)
                    logger.error("Failed to arm reservation for %s: %s", proj_id, e)
            else:
                logger.info(
                    f"[arm?]    {proj_id} would ARM "
                    f"(psi={cl:.1f} p_hit={ph:.2f} dur={bd:.2f}s benefit={bn:.2f}s cost={cs:.2f}s)"
                )
        elif d == "holdout":
            logger.info(
                f"[arm?]    {proj_id} HOLDOUT "
                f"(psi={cl:.1f} p_hit={ph:.2f} dur={bd:.2f}s benefit={bn:.2f}s cost={cs:.2f}s)"
            )
        elif d == "abstain_no_contention":
            logger.info(f"[arm?]    {proj_id} abstain: no contention (psi={cl:.1f})")
        elif d == "abstain_confidence":
            logger.info(f"[arm?]    {proj_id} abstain: confidence gate (psi={cl:.1f})")
        elif d == "abstain_cost":
            logger.info(
                f"[arm?]    {proj_id} abstain: cost>benefit "
                f"(psi={cl:.1f} benefit={bn:.2f}s cost={cs:.2f}s)"
            )
        elif d == "abstain_no_build_estimate":
            logger.info(f"[arm?]    {proj_id} abstain: no build estimate yet")
        elif d == "abstain_budget":
            logger.info(f"[arm?]    {proj_id} abstain: boost budget exhausted")
        elif d == "abstain_paused":
            logger.info(f"[arm?]    {proj_id} abstain: kill switch active (paused)")
        elif d == "abstain_circuit_breaker":
            logger.info(f"[arm?]    {proj_id} abstain: circuit breaker active")
            logger.info(f"[arm?]    {proj_id} abstain: no build estimate yet")

        # Store the decision on the prediction row
        try:
            self.store.set_prediction_decision(
                prediction_id=pred_id,
                decision=d,
                contention_level=cl,
                benefit_s=bn,
                cost_s=cs,
            )
            if arm_decision.holdout:
                self.store.set_prediction_holdout(pred_id, True)
        except Exception as e:
            logger.warning("Failed to store arming decision for prediction #%s: %s", pred_id, e)

    def _on_process_batch(self, batch: List[ProcessInfo]) -> None:
        """Callback invoked by ProcessWatcher for each scan batch of new processes."""
        with self._lock:
            enabled_projects = [p for p in self.store.list_projects() if p.enabled]
            if not enabled_projects or not batch:
                return

            candidates_by_proj: Dict[str, List[ProcessInfo]] = collections.defaultdict(list)

            for proc in batch:
                matching_projs = []
                for proj in enabled_projects:
                    profile = resolve_tool_profile(proj.tool_profile_name)
                    if match(proc, proj.root_path, profile):
                        matching_projs.append(proj)

                if matching_projs:
                    matching_projs.sort(key=lambda p: len(str(Path(p.root_path).resolve())), reverse=True)
                    best_proj = matching_projs[0]
                    candidates_by_proj[best_proj.id].append(proc)

            proc_table = self.watcher.get_process_table()

            for proj_id, candidates in candidates_by_proj.items():
                top = resolve_topmost_ancestor(candidates, proc_table)
                if top is None:
                    top = sorted(candidates, key=lambda p: p.t_start_monotonic)[0]

                # Maintain 2-second spawn buffer
                now_mono = self.clock()
                self._spawn_buffer[proj_id].append((top.t_start_monotonic, top))
                self._spawn_buffer[proj_id] = [
                    s for s in self._spawn_buffer[proj_id] if now_mono - s[0] <= 2.0
                ]

                # Dispatch according to baseline condition
                if self.condition == "c1-reactive":
                    self._handle_reactive_spawn(proj_id, top, top.t_start_monotonic, proc_table)
                elif self.condition == "c3-static":
                    self._handle_static_spawn(proj_id, top, top.t_start_monotonic, proc_table)
                else:
                    # c2-full: match against pending save if active
                    pending = self.predictor._pending_saves.get(proj_id)
                    if pending is not None and not pending.spawn_handled:
                        if top.t_start_monotonic >= pending.t_save_monotonic:
                            self._handle_matched_spawn(proj_id, top, top.t_start_monotonic)

    def _start_tracking_build(
        self,
        pid: int,
        project_id: str,
        condition: str,
        contention_level: float,
        prediction_id: Optional[int],
        res_id: Optional[str],
        t_start_mono: float,
        proc_info: ProcessInfo,
    ) -> None:
        """Register active build tracking, pidfd exit monitor, and save suppression."""
        proc_name = proc_info.exe_name if proc_info.exe_name else (proc_info.exe.name if proc_info.exe else "unknown")
        cmd_0 = proc_info.cmdline[0] if proc_info.cmdline else ""

        self._tracked_builds[pid] = _TrackedBuild(
            pid=pid,
            project_id=project_id,
            t_start_wall=time.time(),
            t_start_mono=t_start_mono,
            contention_level=contention_level,
            prediction_id=prediction_id,
            proc_name=proc_name,
            ppid=proc_info.ppid,
            cmdline_0=cmd_0,
            condition=condition,
            reservation_id=res_id,
        )

        self._exit_monitor.register_pid(pid)
        self.monitor.suppress_project(project_id, True)

    def _handle_reactive_spawn(
        self,
        project_id: str,
        proc: ProcessInfo,
        t_start_mono: float,
        proc_table: Optional[Dict[int, Any]] = None,
    ) -> None:
        """Handle matched build process under C1-reactive condition (boost on contention, no prediction)."""
        psi_metrics = read_psi_cpu()
        cl = psi_metrics.some_avg10 if psi_metrics is not None else 0.0
        should_boost = (not self.contention_gate_enabled) or (cl >= 10.0)

        claimed = False
        res_id = None
        if should_boost and self.actuate and self.reservation_manager.available:
            if not self.safety_governor.is_paused() and not self.safety_governor.is_circuit_breaker_active():
                if self.safety_governor.check_and_consume_budget(60.0, store=self.store):
                    try:
                        res = self.reservation_manager.arm(
                            project_id=project_id,
                            prediction_id=None,
                            tool_profile="default",
                            t_save_mono=t_start_mono,
                            window_lo=0.0,
                            window_hi=60.0,
                            median_build_dur=10.0,
                            weight=self.reservation_weight,
                            reason=f"c1-reactive psi={cl:.1f}",
                        )
                        if res is not None:
                            res_id = res.id
                            t_claim_start = self.clock()
                            if self.reservation_manager.try_claim(project_id, proc, proc_table):
                                t_claim_end = self.clock()
                                claimed = True
                                self.safety_governor.record_actuation_success()
                                lat_ms = (res.claim_latency_s * 1000.0) if res.claim_latency_s is not None else 0.0
                                logger.info(
                                    f"[claim]  {project_id} res={res.id} pid={proc.pid} latency={lat_ms:.1f}ms (c1-reactive)"
                                )
                                self._log_claim_breakdown(res.id, proc, t_claim_start, t_claim_end)
                    except Exception as e:
                        self.safety_governor.record_actuation_failure(e)
                        logger.warning("C1-reactive actuation error: %s", e)

        condition = "reserved" if claimed else "observed"
        self._start_tracking_build(
            pid=proc.pid,
            project_id=project_id,
            condition=condition,
            contention_level=cl,
            prediction_id=None,
            res_id=res_id,
            t_start_mono=t_start_mono,
            proc_info=proc,
        )

    def _handle_static_spawn(
        self,
        project_id: str,
        proc: ProcessInfo,
        t_start_mono: float,
        proc_table: Optional[Dict[int, Any]] = None,
    ) -> None:
        """Handle matched build process under C3-static condition (always-on boost, always intensified)."""
        claimed = False
        res_id = None
        if self.actuate and self.reservation_manager.available:
            if not self.safety_governor.is_paused() and not self.safety_governor.is_circuit_breaker_active():
                if self.safety_governor.check_and_consume_budget(60.0, store=self.store):
                    try:
                        res = self.reservation_manager.arm(
                            project_id=project_id,
                            prediction_id=None,
                            tool_profile="default",
                            t_save_mono=t_start_mono,
                            window_lo=0.0,
                            window_hi=60.0,
                            median_build_dur=10.0,
                            weight=self.reservation_weight,
                            reason="c3-static",
                        )
                        if res is not None:
                            res_id = res.id
                            t_claim_start = self.clock()
                            if self.reservation_manager.try_claim(project_id, proc, proc_table):
                                t_claim_end = self.clock()
                                claimed = True
                                self.safety_governor.record_actuation_success()
                                lat_ms = (res.claim_latency_s * 1000.0) if res.claim_latency_s is not None else 0.0
                                logger.info(
                                    f"[claim]  {project_id} res={res.id} pid={proc.pid} latency={lat_ms:.1f}ms (c3-static)"
                                )
                                self._log_claim_breakdown(res.id, proc, t_claim_start, t_claim_end)
                    except Exception as e:
                        self.safety_governor.record_actuation_failure(e)
                        logger.warning("C3-static actuation error: %s", e)

        condition = "reserved" if claimed else "observed"
        self._start_tracking_build(
            pid=proc.pid,
            project_id=project_id,
            condition=condition,
            contention_level=0.0,
            prediction_id=None,
            res_id=res_id,
            t_start_mono=t_start_mono,
            proc_info=proc,
        )

    def _handle_matched_spawn(
        self,
        project_id: str,
        proc: ProcessInfo,
        t_start_mono: float,
    ) -> None:
        """Process a validated spawn matching a pending save for a project."""
        pending = self.predictor._pending_saves.get(project_id)
        if pending is None or pending.spawn_handled:
            return

        pred_id = pending.prediction_id
        delay = t_start_mono - pending.t_save_monotonic
        file_class = pending.file_class
        tool_profile = pending.tool_profile

        # 1. Prediction outcome scoring
        res = self.predictor.on_process_spawn(project_id, t_start_mono)
        if res is not None:
            sample_count = self.store.sample_count(project_id, file_class, tool_profile)
            status_str = "accepted" if res.accepted else "rejected"
            if proc.exe_name:
                cmd_name = proc.exe_name
            elif proc.cmdline:
                cmd_name = proc.cmdline[0] if isinstance(proc.cmdline, list) else proc.cmdline.split()[0]
            else:
                cmd_name = "process"
            logger.info(
                f"[spawn]   {project_id} {cmd_name} pid={proc.pid} delay={delay:.2f}s -> sample #{sample_count} {status_str}"
            )
            if pred_id is not None:
                if pending.window_lo is not None and pending.window_hi is not None:
                    is_hit = (pending.window_lo <= delay <= pending.window_hi)
                    outcome = "hit" if is_hit else "miss"
                else:
                    outcome = "no_build" if delay > self.predictor.config.correlation_window_s else "miss"
                logger.info(f"[outcome] {project_id} prediction #{pred_id} {outcome} (delay {delay:.2f}s)")

        # 2. Reservation claim check if actuating
        claimed = False
        res_id: Optional[str] = None
        active_res = self.reservation_manager.get_reservation_for_project(project_id)
        if (
            self.actuate
            and not self.safety_governor.is_paused()
            and active_res is not None
            and active_res.state in (ReservationState.ARMED, ReservationState.ACTIVE)
        ):
            proc_table = self.watcher.get_process_table()
            res_id = active_res.id
            try:
                t_claim_start = self.clock()
                if self.reservation_manager.try_claim(project_id, proc, proc_table):
                    t_claim_end = self.clock()
                    claimed = True
                    self.safety_governor.record_actuation_success()
                    lat_ms = (active_res.claim_latency_s * 1000.0) if active_res.claim_latency_s is not None else 0.0
                    logger.info(
                        f"[claim]  {project_id} res={active_res.id} pid={proc.pid} latency={lat_ms:.1f}ms "
                        f"group={active_res.group_name} original={active_res.original_cgroup}"
                    )
                    self._log_claim_breakdown(active_res.id, proc, t_claim_start, t_claim_end)
                    # Start background leak-checker thread if opt-in instrumentation is enabled
                    if self.leak_check:
                        self._start_leak_checker(
                            project_id=project_id,
                            claimed_pid=proc.pid,
                            starttime_ticks=proc.starttime_ticks,
                            group_name=active_res.group_name,
                        )
            except Exception as e:
                self.safety_governor.record_actuation_failure(e)
                logger.warning("Failed to claim reservation for %s: %s", project_id, e)

        # 3. Determine build condition ('reserved', 'holdout', 'observed')
        if claimed:
            condition = "reserved"
        elif pred_id is not None:
            # Check if prediction was holdout
            pred_row = self.store.get_prediction(pred_id)
            if pred_row and (pred_row.get("holdout") or pred_row.get("decision") == "holdout"):
                condition = "holdout"
            else:
                condition = "observed"
        else:
            condition = "observed"

        # 4. Register build tracking and pidfd exit monitor
        psi_metrics = read_psi_cpu()
        contention = psi_metrics.some_avg10 if psi_metrics is not None else 0.0

        self._start_tracking_build(
            pid=proc.pid,
            project_id=project_id,
            condition=condition,
            contention_level=contention,
            prediction_id=pred_id,
            res_id=res_id,
            t_start_mono=t_start_mono,
            proc_info=proc,
        )

    def _log_claim_breakdown(
        self,
        res_id: str,
        proc: ProcessInfo,
        t_claim_start: float,
        t_claim_end: float,
    ) -> None:
        mode_str = "intensified" if self.watcher.is_intensified() else "normal"
        det_ms = max(0.0, proc.t_seen_monotonic - proc.t_start_monotonic) * 1000.0
        disp_ms = max(0.0, t_claim_start - proc.t_seen_monotonic) * 1000.0
        exec_ms = max(0.0, t_claim_end - t_claim_start) * 1000.0
        tot_ms = max(0.0, t_claim_end - proc.t_start_monotonic) * 1000.0
        logger.info(
            f"[claim_breakdown] res={res_id} pid={proc.pid} det_lat={det_ms:.1f}ms "
            f"disp_lat={disp_ms:.1f}ms exec_lat={exec_ms:.1f}ms total_lat={tot_ms:.1f}ms mode={mode_str}"
        )

    def _on_process_exit(self, pid: int, t_exit_mono: float) -> None:
        """Callback invoked immediately by ProcessExitMonitor when a tracked PID exits."""
        with self._lock:
            tb = self._tracked_builds.pop(pid, None)
            if tb is None:
                return

            # Sub-millisecond duration calculation: t_exit_mono - tb.t_start_mono
            duration = max(0.001, t_exit_mono - tb.t_start_mono)
            t_end_wall = time.time()
            t_start_wall = t_end_wall - duration

            self.store.record_build(
                project_id=tb.project_id,
                condition=tb.condition,
                contention_level=tb.contention_level,
                duration_s=duration,
                t_start_wall=t_start_wall,
                t_end_wall=t_end_wall,
                prediction_id=tb.prediction_id,
            )

            # Trigger benefit estimation evaluation if enough new builds have accumulated
            if self.benefit_estimator is not None:
                try:
                    self.benefit_estimator.maybe_evaluate(tb.project_id)
                except Exception as e:
                    logger.warning("Error evaluating benefit estimation for %s: %s", tb.project_id, e)

            logger.info(
                f"[build]   {tb.project_id} done {duration:.2f}s condition={tb.condition} psi={tb.contention_level:.1f} "
                f"proc={tb.proc_name} pid={tb.pid} ppid={tb.ppid} cmd0={tb.cmdline_0}"
            )

            # Advance reservation manager immediately to detect empty cgroup
            if self.actuate:
                transitioned = self.reservation_manager.tick(t_exit_mono)
                for tr in transitioned:
                    if tr.state in (ReservationState.CLOSED, ReservationState.EXPIRED, ReservationState.CANCELLED):
                        self._persist_reservation(tr)

            # Retain save suppression for +0.3s tail
            self._unsuppress_at[tb.project_id] = t_exit_mono + 0.3

    def _persist_reservation(self, res: ReservationRecord) -> None:
        """Persist terminal reservation record to store."""
        try:
            self.store.record_reservation(res)
        except Exception as e:
            logger.warning("Failed to record reservation %s: %s", res.id, e)

    def _start_leak_checker(
        self,
        project_id: str,
        claimed_pid: int,
        starttime_ticks: Optional[int],
        group_name: str,
    ) -> None:
        """Spawn a daemon thread that logs [leak] counts at +0.3s and +1.0s after claim.

        Walks real /proc (not the watcher table) to find all descendants of the
        claimed PID, then checks each one's /proc/<pid>/cgroup. Processes NOT in
        *group_name* are counted and their names logged. Instrumentation only —
        does NOT move any processes.
        """
        from precompile.reservation import _get_all_descendants_proc

        def _checker() -> None:
            t_claim_mono = time.monotonic()
            checkpoints = [0.5, 1.5]
            proc_path = Path("/proc")
            for delay in checkpoints:
                target_mono = t_claim_mono + delay
                sleep_s = max(0.0, target_mono - time.monotonic())
                if sleep_s > 0:
                    time.sleep(sleep_s)
                try:
                    descendants = _get_all_descendants_proc(claimed_pid, proc_path, starttime_ticks)
                    leaked: List[str] = []
                    for dpid in sorted(descendants):
                        cgroup_file = proc_path / str(dpid) / "cgroup"
                        comm_file = proc_path / str(dpid) / "comm"
                        try:
                            cg = cgroup_file.read_text(encoding="utf-8", errors="replace").strip()
                            in_res = group_name in cg
                        except OSError:
                            continue  # process already gone
                        if not in_res:
                            try:
                                name = comm_file.read_text(encoding="utf-8", errors="replace").strip()
                            except OSError:
                                name = str(dpid)
                            leaked.append(f"{name}({dpid})")
                    logger.info(
                        "[leak] +%.1fs %s res=%s claimed_pid=%d descendants=%d leaked=%d (%s)",
                        delay,
                        project_id,
                        group_name,
                        claimed_pid,
                        len(descendants),
                        len(leaked),
                        ", ".join(leaked[:10]) if leaked else "none",
                    )
                except Exception as e:
                    logger.debug("[leak] checker error at +%.1fs: %s", delay, e)

        t = threading.Thread(target=_checker, daemon=True, name=f"leak-checker-{claimed_pid}")
        t.start()

    def _run_housekeeping(self) -> None:
        """Housekeeping worker thread running every 50 ms."""
        while not self._stop_event.is_set():
            now_mono = self.clock()

            with self._lock:
                # 0. Sample PSI continuously every ~200 ms
                if now_mono - self._last_psi_sample_mono >= 0.20:
                    self._arming_gate.sample_psi()
                    self._last_psi_sample_mono = now_mono

                # 1. Expire pending saves
                expired_ids = self.predictor.expire_pending(now_mono)
                for exp_id in expired_ids:
                    logger.info(f"[outcome] prediction #{exp_id} no_build")

                # 2. Advance reservation manager state machine
                # Note: Kill switch stops new arming and claiming, but allows running
                # reservations to finish and close normally.
                if self.actuate:
                    try:
                        transitioned = self.reservation_manager.tick(now_mono)
                        for tr in transitioned:
                            if tr.state in (ReservationState.CLOSED, ReservationState.EXPIRED, ReservationState.CANCELLED):
                                self._persist_reservation(tr)
                    except Exception as e:
                        self.safety_governor.record_actuation_failure(e)
                        logger.warning("Error in reservation_manager.tick: %s", e)

                # 3. Watcher intensified mode check:
                if self.condition == "c3-static":
                    self.watcher.set_intensified(True)
                else:
                    active_window = False
                    with self.predictor._lock:
                        for pending in self.predictor._pending_saves.values():
                            if pending.window_lo is not None and pending.window_hi is not None:
                                w_start = pending.t_save_monotonic + pending.window_lo
                                w_end = pending.t_save_monotonic + pending.window_hi
                                if w_start <= now_mono <= w_end:
                                    active_window = True
                                    break

                    has_active_res = bool(self.reservation_manager.list_active())
                    self.watcher.set_intensified(active_window or has_active_res)

                # 4. Fallback check for build completion (in case pidfd missed or unsupported)
                for pid, tb in list(self._tracked_builds.items()):
                    pid_path = self.proc_path / str(pid)
                    if not pid_path.exists():
                        self._exit_monitor.unregister_pid(pid)
                        duration = max(0.001, now_mono - tb.t_start_mono)
                        t_end_wall = time.time()
                        t_start_wall = t_end_wall - duration
                        self.store.record_build(
                            project_id=tb.project_id,
                            condition=tb.condition,
                            contention_level=tb.contention_level,
                            duration_s=duration,
                            t_start_wall=t_start_wall,
                            t_end_wall=t_end_wall,
                            prediction_id=tb.prediction_id,
                        )
                        logger.info(
                            f"[build]   {tb.project_id} done {duration:.2f}s condition={tb.condition} psi={tb.contention_level:.1f} "
                            f"proc={tb.proc_name} pid={tb.pid} ppid={tb.ppid} cmd0={tb.cmdline_0}"
                        )
                        del self._tracked_builds[pid]
                        self._unsuppress_at[tb.project_id] = now_mono + 0.3

                # 5. Check suppression tail expirations
                for proj_id, deadline in list(self._unsuppress_at.items()):
                    if now_mono >= deadline:
                        if not any(b.project_id == proj_id for b in self._tracked_builds.values()):
                            self.monitor.suppress_project(proj_id, False)
                        del self._unsuppress_at[proj_id]

            self._stop_event.wait(0.05)

    def reload_projects(self) -> None:
        """Reload project list on SIGHUP or programmatically."""
        with self._lock:
            try:
                current_projects = self.store.list_projects()
            except Exception as e:
                logger.warning("SIGHUP reload: failed to list projects: %s", e)
                return

            desired_watched: Set[str] = {
                p.id for p in current_projects if p.enabled
            }

            for proj in current_projects:
                if proj.enabled and proj.id not in self._watched_project_ids:
                    try:
                        self.monitor.watch_project(proj.id, proj.root_path)
                        self._watched_project_ids.add(proj.id)
                        logger.info("SIGHUP: added watch for project '%s'", proj.id)
                    except Exception as e:
                        logger.warning("SIGHUP: failed to add watch for project '%s': %s", proj.id, e)

            for proj_id in list(self._watched_project_ids):
                if proj_id not in desired_watched:
                    try:
                        self.monitor.unwatch_project(proj_id)
                        self._watched_project_ids.discard(proj_id)
                        logger.info("SIGHUP: removed watch for project '%s'", proj_id)
                    except Exception as e:
                        logger.warning("SIGHUP: failed to remove watch for project '%s': %s", proj_id, e)

        logger.info("SIGHUP: project reload complete (%d watched)", len(self._watched_project_ids))

    def start(self) -> None:
        """Start daemon, file watches, exit monitor, and housekeeping loop."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()

            # Record daemon execution session in database (Stage 10)
            run_flags = {
                "condition": self.condition,
                "ablation_no_confidence_gate": self.ablation_no_confidence_gate,
                "ablation_no_contention_gate": self.ablation_no_contention_gate,
                "ablation_fixed_window": self.ablation_fixed_window,
                "ablation_no_holdout": self.ablation_no_holdout,
                "ablation_no_atomic_claim": self.ablation_no_atomic_claim,
                "actuate": self.actuate,
                "reservation_weight": self.reservation_weight,
            }
            try:
                import precompile
                code_ver = getattr(precompile, "__version__", "0.1.0")
                self._daemon_run_id = self.store.record_daemon_run(
                    condition=self.condition,
                    flags_json=json.dumps(run_flags),
                    code_version=code_ver,
                )
                logger.info("[daemon] Registered daemon run #%d (condition=%s)", self._daemon_run_id, self.condition)
            except Exception as e:
                logger.debug("Failed to record daemon run: %s", e)

            # Clean stale cgroup reservations at start via SafetyGovernor
            try:
                stale = self.safety_governor.clean_stale_cgroups()
                if stale:
                    logger.info("[safety] Cleaned %d stale cgroup(s) at startup", len(stale))
            except Exception as e:
                logger.warning("Could not clean stale cgroups at start: %s", e)

            if self.actuate and hasattr(self.reservation_manager.cgroup_backend, "cleanup_stale"):
                try:
                    cleaned = self.reservation_manager.cgroup_backend.cleanup_stale()
                    if cleaned > 0:
                        logger.info("Cleaned %d stale reservation cgroup(s) at startup", cleaned)
                except Exception as e:
                    logger.warning("Could not clean stale cgroups at start: %s", e)

            # Write PID file if requested
            if self.pid_file is not None:
                try:
                    self.pid_file.parent.mkdir(parents=True, exist_ok=True)
                    self.pid_file.write_text(str(os.getpid()), encoding="utf-8")
                except OSError as e:
                    logger.warning("Could not write pid file '%s': %s", self.pid_file, e)

            for proj in self.store.list_projects():
                if proj.enabled:
                    try:
                        self.monitor.watch_project(proj.id, proj.root_path)
                        self._watched_project_ids.add(proj.id)
                    except Exception as e:
                        logger.warning("Failed to watch project '%s': %s", proj.id, e)

            self.monitor.start()
            self.watcher.start()
            self._exit_monitor.start()

            self._housekeeping_thread = threading.Thread(
                target=self._run_housekeeping,
                daemon=True,
                name="precompile-daemon-housekeeping",
            )
            self._housekeeping_thread.start()

            mode_str = "actuation mode" if self.actuate else "observation-only mode"
            logger.info("PreCompile daemon started in %s.", mode_str)

    def stop(self) -> None:
        """Stop daemon cleanly, cancel active reservations, and release all resources."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()

        # Signal all components concurrently to stop immediately
        try:
            self._exit_monitor._stop_event.set()
            self._exit_monitor._notify_wake()
        except Exception:
            pass
        try:
            self.watcher._stop_event.set()
        except Exception:
            pass
        try:
            with self.monitor._lock:
                self.monitor._running = False
            self.monitor._wakeup()
        except Exception:
            pass

        # Stop housekeeping thread
        if self._housekeeping_thread is not None and self._housekeeping_thread.is_alive():
            self._housekeeping_thread.join(timeout=1.0)
        self._housekeeping_thread = None

        # Cancel all reservations cleanly on shutdown
        if hasattr(self, "reservation_manager"):
            try:
                self.reservation_manager.cancel_all(reason="daemon_shutdown")
            except Exception as e:
                logger.warning("Error cancelling reservations on shutdown: %s", e)

        self._exit_monitor.stop()
        self.monitor.stop()
        self.watcher.stop()

        # Log watcher overhead statistics
        stats = self.watcher.get_stats()
        logger.info(
            f"[shutdown] Watcher stats: scans={stats.scans_performed}, "
            f"avg_scan={stats.avg_scan_duration_ms:.2f}ms, "
            f"max_scan={stats.max_scan_duration_ms:.2f}ms, "
            f"processes_seen={stats.processes_seen}, "
            f"processes_missed={stats.processes_missed}, "
            f"normal_scans={stats.scans_normal} ({stats.time_normal_s:.1f}s), "
            f"intensified_scans={stats.scans_intensified} ({stats.time_intensified_s:.1f}s)"
        )

        self.store.close()

        # Remove PID file
        if self.pid_file is not None and self.pid_file.exists():
            try:
                self.pid_file.unlink()
            except OSError:
                pass

        logger.info("PreCompile daemon stopped cleanly.")

    def run_forever(self) -> None:
        """Block until SIGINT, SIGTERM, or SIGHUP is received."""
        def _handle_term(signum: int, frame: Any) -> None:
            logger.info("Received termination signal %d, shutting down...", signum)
            self.stop()
            sys.exit(0)

        def _handle_hup(signum: int, frame: Any) -> None:
            logger.info("Received SIGHUP, reloading project list...")
            self.reload_projects()

        try:
            signal.signal(signal.SIGINT, _handle_term)
            signal.signal(signal.SIGTERM, _handle_term)
            try:
                signal.signal(signal.SIGHUP, _handle_hup)
            except (ValueError, AttributeError, OSError):
                pass
        except (ValueError, AttributeError):
            pass

        self.start()

        try:
            while not self._stop_event.is_set():
                time.sleep(0.1)
        except (KeyboardInterrupt, SystemExit):
            self.stop()
            sys.exit(0)
