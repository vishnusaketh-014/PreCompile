"""Gating mechanisms: Confidence gating and Contention-gated arming.

Stage Mapping: Stage 4 (Confidence Gate) & Stage 5 (Contention-Gated, Cost-Aware Arming).
Enforces statistical confidence thresholds before predicting and validates system contention
(CPU PSI) and benefit-versus-cost balance before arming reservations.

Exact Gate Thresholds:
    Confidence:
        sample_count >= 5
        CV <= 0.5 OR sigma <= 0.25s (for very small means)
        hit_rate >= 0.6 over last 10 predictions (once >= 5 predictions exist)
    Contention (Stage 5):
        contention_level = max(some_avg10, recent_some_pct)
        PRESENT if contention_level >= contention_threshold_pct (default 10.0)
        PREDICTED (treated as present) if:
            some_avg10 in [0.5*threshold, threshold) AND some_avg10 > some_avg60  (rising)
    Cost/Benefit (explicit PRIORS; Step 9 replaces speedup_prior with measured benefit):
        benefit_s = p_hit * build_dur * speedup_frac
        cost_s    = (1 - p_hit) * window_width * idle_cost_rate
                  + p_hit * build_dur * other_impact_rate
        Arm only if benefit_s > cost_s
    Holdout:
        Among events passing both gates, withhold holdout_probability share as holdout.
"""

import collections
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Deque, List, Optional, Tuple, Union

from precompile.config import PredictorConfig

if TYPE_CHECKING:
    from precompile.predictor import LatencyModel, ModelStats


@dataclass
class GateResult:
    """Result of evaluating confidence gate for a model."""
    can_predict: bool
    can_arm: bool
    reasons: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        """Allow evaluating GateResult directly in boolean expressions as can_predict."""
        return self.can_predict


@dataclass
class CPUContentionMetrics:
    """CPU pressure stall information parsed from /proc/pressure/cpu."""
    some_avg10: float
    some_avg60: float
    some_avg300: float
    some_total: int          # microseconds, same as some_total_us
    full_avg10: float = 0.0
    full_avg60: float = 0.0
    full_avg300: float = 0.0
    full_total: int = 0      # microseconds

    @property
    def some_total_us(self) -> int:
        """Alias for some_total (microseconds of accumulated stall time)."""
        return self.some_total


def parse_psi_cpu(text: str) -> Optional[CPUContentionMetrics]:
    """Parse raw /proc/pressure/cpu text content into CPUContentionMetrics.

    Returns None if text is empty or missing 'some avg10'.
    """
    some_metrics: dict = {}
    full_metrics: dict = {}

    for line in text.strip().splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        prefix = parts[0]
        if prefix == "some":
            target = some_metrics
        elif prefix == "full":
            target = full_metrics
        else:
            continue

        for item in parts[1:]:
            if "=" in item:
                k, v = item.split("=", 1)
                try:
                    target[k] = float(v)
                except ValueError:
                    pass

    if "avg10" not in some_metrics:
        return None

    return CPUContentionMetrics(
        some_avg10=some_metrics.get("avg10", 0.0),
        some_avg60=some_metrics.get("avg60", 0.0),
        some_avg300=some_metrics.get("avg300", 0.0),
        some_total=int(some_metrics.get("total", 0)),
        full_avg10=full_metrics.get("avg10", 0.0),
        full_avg60=full_metrics.get("avg60", 0.0),
        full_avg300=full_metrics.get("avg300", 0.0),
        full_total=int(full_metrics.get("total", 0)),
    )


def read_psi_cpu(path: Union[str, Path] = "/proc/pressure/cpu") -> Optional[CPUContentionMetrics]:
    """Read and parse /proc/pressure/cpu. Returns None if unavailable or unparseable."""
    try:
        p = Path(path)
        if not p.exists():
            return None
        text = p.read_text(encoding="utf-8")
        return parse_psi_cpu(text)
    except (OSError, PermissionError, ValueError):
        return None


class PSISampler:
    """Maintains a short history of (monotonic_time, some_total_us) samples.

    Computes recent_some_pct = delta_stall_time / delta_wall_time over the
    most recent window of >= 1 second. This is the *instantaneous* PSI rate,
    complementing the kernel's exponentially weighted avg10.

    Injectable clock and reader for testing.
    """

    def __init__(
        self,
        reader: Optional[Callable[[], Optional[CPUContentionMetrics]]] = None,
        clock: Callable[[], float] = time.monotonic,
        history_duration_s: float = 10.0,
    ) -> None:
        """Initialize PSISampler.

        Args:
            reader: Callable returning CPUContentionMetrics or None. Defaults to read_psi_cpu.
            clock: Monotonic clock callable.
            history_duration_s: How many seconds of history to keep.
        """
        self._reader = reader if reader is not None else read_psi_cpu
        self._clock = clock
        self._history_duration_s = history_duration_s
        # Deque of (monotonic_time_s, some_total_us)
        self._history: Deque[Tuple[float, int]] = collections.deque()

    def sample(self) -> Optional[CPUContentionMetrics]:
        """Read current PSI metrics and record in history. Returns metrics or None."""
        metrics = self._reader()
        if metrics is None:
            return None
        now = self._clock()
        self._history.append((now, metrics.some_total_us))
        # Evict samples older than history_duration_s
        cutoff = now - self._history_duration_s
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()
        return metrics

    @property
    def recent_some_pct(self) -> float:
        """Compute recent CPU stall percentage over the last >= 1 second of history.

        Returns:
            Float in [0.0, 100.0] representing the fraction of wall time that
            tasks spent stalled on CPU. Returns 0.0 if insufficient history.
        """
        if len(self._history) < 2:
            return 0.0
        newest_t, newest_stall = self._history[-1]
        min_window = newest_t - 1.0
        # If total span of history is less than 1 second, return 0.0
        if self._history[0][0] > min_window:
            return 0.0

        oldest_idx = 0
        for i, (t, _) in enumerate(self._history):
            if t <= min_window:
                oldest_idx = i
            else:
                break
        oldest_t, oldest_stall = self._history[oldest_idx]
        wall_delta = newest_t - oldest_t
        if wall_delta < 1e-6:
            return 0.0
        stall_delta_us = newest_stall - oldest_stall
        if stall_delta_us < 0:
            # Wraparound or reset; return 0
            return 0.0
        # some_total is in microseconds; wall_delta is in seconds
        stall_delta_s = stall_delta_us / 1_000_000.0
        return min(100.0, (stall_delta_s / wall_delta) * 100.0)


@dataclass
class ArmDecision:
    """Result of ArmingGate.decide() call.

    decision values:
        'would_arm'                 -- all gates passed, would arm (or holdout selected)
        'holdout'                   -- gates passed but withheld as holdout for evaluation
        'abstain_confidence'        -- confidence gate blocked arming
        'abstain_no_contention'     -- contention gate: PSI below threshold
        'abstain_cost'              -- cost/benefit gate: cost >= benefit
        'abstain_no_build_estimate' -- no observed builds to estimate duration
    """
    decision: str
    arm: bool           # True only if decision == 'would_arm' (gates pass, not holdout)
    holdout: bool       # True if gates passed but event is withheld as holdout
    reasons: List[str]
    contention_level: float   # percent
    p_hit: float
    build_dur: float          # seconds (median of last 10 observed builds; 0.0 if unavailable)
    window_width: float       # seconds (window_hi - window_lo)
    benefit_s: float          # seconds
    cost_s: float             # seconds


class ConfidenceGate:
    """Evaluates whether learned latency distribution is sufficiently reliable to predict."""

    def __init__(
        self,
        config: Optional[PredictorConfig] = None,
        min_samples: Optional[int] = None,
        cv_max: Optional[float] = None,
        sigma_max_small_s: Optional[float] = None,
        hit_rate_min: Optional[float] = None,
        hit_rate_last: Optional[int] = None,
        min_predictions: Optional[int] = None,
        strict_min_predictions_to_arm: Optional[bool] = None,
        # Legacy aliases
        max_cv: Optional[float] = None,
        small_mean_sigma_threshold: Optional[float] = None,
        min_hit_rate: Optional[float] = None,
        hit_rate_eval_threshold: Optional[int] = None,
    ) -> None:
        """Initialize ConfidenceGate with spec thresholds."""
        cfg = config if config is not None else PredictorConfig()
        self.min_samples = min_samples if min_samples is not None else cfg.min_samples
        self.cv_max = cv_max if cv_max is not None else (max_cv if max_cv is not None else cfg.cv_max)
        self.sigma_max_small_s = (
            sigma_max_small_s
            if sigma_max_small_s is not None
            else (small_mean_sigma_threshold if small_mean_sigma_threshold is not None else cfg.sigma_max_small_s)
        )
        self.hit_rate_min = (
            hit_rate_min
            if hit_rate_min is not None
            else (min_hit_rate if min_hit_rate is not None else cfg.hit_rate_min)
        )
        self.hit_rate_last = hit_rate_last if hit_rate_last is not None else cfg.hit_rate_last
        self.min_predictions = (
            min_predictions
            if min_predictions is not None
            else (hit_rate_eval_threshold if hit_rate_eval_threshold is not None else cfg.min_predictions)
        )
        self.strict_min_predictions_to_arm = (
            strict_min_predictions_to_arm
            if strict_min_predictions_to_arm is not None
            else cfg.strict_min_predictions_to_arm
        )

    def evaluate(
        self,
        model: Any,
        hit_stats: Union[Tuple[int, int, float], float, None] = None,
        total_predictions_made: Optional[int] = None,
    ) -> GateResult:
        """Evaluate whether the confidence gate passes.

        Args:
            model: Current latency model (LatencyModel or ModelStats).
            hit_stats: Tuple of (hits, scored_total, rate), or recent_hit_rate float.
            total_predictions_made: Total predictions logged (if hit_stats is float).

        Returns:
            GateResult with can_predict, can_arm, and diagnostic reasons.
        """
        n = getattr(model, "n", getattr(model, "sample_count", 0))
        mean = getattr(model, "mean", 0.0)
        sigma = getattr(model, "sigma", 0.0)
        cv = getattr(model, "cv", None)

        if isinstance(hit_stats, tuple):
            hits, scored_total, rate = hit_stats
        elif isinstance(hit_stats, (int, float)) and total_predictions_made is not None:
            rate = float(hit_stats)
            scored_total = int(total_predictions_made)
            hits = int(round(rate * scored_total))
        elif isinstance(hit_stats, (int, float)):
            rate = float(hit_stats)
            scored_total = self.min_predictions
            hits = int(round(rate * scored_total))
        else:
            hits, scored_total, rate = 0, 0, 0.0

        reasons: List[str] = []
        can_predict = True

        # 1. Sample count check
        if n < self.min_samples:
            can_predict = False
            reasons.append(f"Insufficient samples: n={n} < min_samples={self.min_samples}")

        # 2. Dispersion check
        if mean <= 1e-9:
            # When mean is zero or near zero, CV is undefined; evaluate sigma only
            if sigma > self.sigma_max_small_s:
                can_predict = False
                reasons.append(
                    f"Sigma too high for small mean: sigma={sigma:.4f} > {self.sigma_max_small_s}"
                )
        else:
            cv_ok = (cv is not None and cv <= self.cv_max)
            sigma_ok = (sigma <= self.sigma_max_small_s)
            if not (cv_ok or sigma_ok):
                can_predict = False
                reasons.append(
                    f"High variance: cv={cv:.4f} > {self.cv_max} and sigma={sigma:.4f} > {self.sigma_max_small_s}"
                )

        # 3. Arming check
        can_arm = False
        if not can_predict:
            reasons.append("Cannot arm: model did not pass can_predict")
        else:
            if self.strict_min_predictions_to_arm:
                if scored_total < self.min_predictions:
                    reasons.append(
                        f"Observation-only warm-up: scored predictions {scored_total} < {self.min_predictions}"
                    )
                else:
                    if rate < self.hit_rate_min:
                        reasons.append(
                            f"Low hit rate: {rate:.1%} < {self.hit_rate_min:.1%} ({hits}/{scored_total})"
                        )
                    else:
                        can_arm = True
            else:
                # Looser literal reading: hit-rate gate skipped until min_predictions exist
                if scored_total < self.min_predictions:
                    can_arm = True
                else:
                    if rate < self.hit_rate_min:
                        reasons.append(
                            f"Low hit rate: {rate:.1%} < {self.hit_rate_min:.1%} ({hits}/{scored_total})"
                        )
                    else:
                        can_arm = True

        return GateResult(can_predict=can_predict, can_arm=can_arm, reasons=reasons)


class ArmingGate:
    """Evaluates CPU contention and cost-benefit ratio before arming a reservation.

    Implements Stage 5 of the PreCompile spec: contention gate, cost/benefit gate,
    and holdout selector. Decisions are computed and logged but no cgroup is touched
    (observation-only in Step 7).

    Cost/Benefit Model (explicit PRIORS; Step 9 replaces speedup_prior with measured benefit):
        speedup_frac = benefit_prior_max * min(1, contention_level / 50)
        benefit_s    = p_hit * build_dur * speedup_frac
        cost_s       = (1 - p_hit) * window_width * idle_cost_rate
                     + p_hit * build_dur * other_impact_rate
    """

    def __init__(
        self,
        # PSI settings
        psi_cpu_path: Union[str, Path] = "/proc/pressure/cpu",
        contention_threshold_pct: float = 10.0,
        # Cost/benefit priors
        benefit_prior_max: float = 0.5,
        idle_cost_rate: float = 0.05,
        other_impact_rate: float = 0.10,
        # Holdout
        holdout_probability: float = 0.1,
        # Gate enable toggles (Step 10 adds CLI flags; here as config booleans)
        confidence_gate_enabled: bool = True,
        contention_gate_enabled: bool = True,
        cost_gate_enabled: bool = True,
        holdout_enabled: bool = True,
        # Injectables for testing
        psi_reader: Optional[Callable[[], Optional[CPUContentionMetrics]]] = None,
        psi_sampler: Optional["PSISampler"] = None,
        rng: Optional[random.Random] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize ArmingGate.

        Args:
            psi_cpu_path: Path to /proc/pressure/cpu.
            contention_threshold_pct: PSI some_avg10 threshold in percent (default 10.0).
            benefit_prior_max: Maximum fraction of build time saved under full contention (default 0.5).
            idle_cost_rate: Fraction of window_width counted as cost for a miss (default 0.05).
            other_impact_rate: Fraction of build_dur counted as overhead cost for a hit (default 0.10).
            holdout_probability: Probability of withholding a qualifying event as holdout (default 0.1).
            confidence_gate_enabled: If False, skip confidence gate check (default True).
            contention_gate_enabled: If False, skip contention gate check (default True).
            cost_gate_enabled: If False, skip cost/benefit gate check (default True).
            holdout_enabled: If False, never select holdout (default True).
            psi_reader: Injectable callable returning CPUContentionMetrics or None.
            psi_sampler: Injectable PSISampler instance (if None, one is created internally).
            rng: Injectable random.Random for holdout selection.
            clock: Monotonic clock callable (default time.monotonic).
        """
        self._psi_path = Path(psi_cpu_path)
        self.contention_threshold_pct = contention_threshold_pct
        self.benefit_prior_max = benefit_prior_max
        self.idle_cost_rate = idle_cost_rate
        self.other_impact_rate = other_impact_rate
        self.holdout_probability = holdout_probability
        self.confidence_gate_enabled = confidence_gate_enabled
        self.contention_gate_enabled = contention_gate_enabled
        self.cost_gate_enabled = cost_gate_enabled
        self.holdout_enabled = holdout_enabled
        self._clock = clock

        # PSI reader: injectable for tests
        if psi_reader is not None:
            self._psi_reader = psi_reader
        else:
            _path = self._psi_path

            def _default_reader() -> Optional[CPUContentionMetrics]:
                return read_psi_cpu(_path)

            self._psi_reader = _default_reader

        # PSI sampler for recent_some_pct
        if psi_sampler is not None:
            self._psi_sampler = psi_sampler
        else:
            self._psi_sampler = PSISampler(reader=self._psi_reader, clock=self._clock)

        # RNG for holdout
        self._rng = rng if rng is not None else random.Random()

    @property
    def psi_sampler(self) -> "PSISampler":
        """Access the underlying PSISampler."""
        return self._psi_sampler

    def sample_psi(self) -> Optional[CPUContentionMetrics]:
        """Trigger an explicit sample of CPU PSI metrics into the sampler history.

        Returns:
            Latest CPUContentionMetrics or None.
        """
        return self._psi_sampler.sample()

    def read_cpu_pressure(self) -> Optional[CPUContentionMetrics]:
        """Read and parse /proc/pressure/cpu via the injectable reader.

        Returns:
            CPUContentionMetrics instance or None if unavailable.
        """
        return self._psi_reader()

    def compute_contention_level(
        self,
        metrics: Optional[CPUContentionMetrics],
        recent_some_pct: float = 0.0,
    ) -> float:
        """Compute contention_level as max(some_avg10, recent_some_pct).

        Args:
            metrics: CPU contention metrics (or None).
            recent_some_pct: Recent instantaneous stall rate from PSISampler.

        Returns:
            contention_level in percent [0.0, 100.0].
        """
        if metrics is None:
            return 0.0
        return max(metrics.some_avg10, recent_some_pct)

    def is_contention_present(
        self,
        metrics: Optional[CPUContentionMetrics],
        recent_some_pct: float = 0.0,
    ) -> Tuple[bool, float]:
        """Determine if current CPU pressure warrants priority reservation.

        Contention is PRESENT if contention_level >= threshold.
        Contention is PREDICTED (treated as present) if:
            some_avg10 is in [0.5*threshold, threshold) AND some_avg10 > some_avg60 (rising).

        Args:
            metrics: Current CPU contention metrics (or None).
            recent_some_pct: Recent instantaneous stall rate from PSISampler.

        Returns:
            Tuple of (is_present_or_predicted: bool, contention_level: float).
        """
        if metrics is None:
            return False, 0.0

        contention_level = self.compute_contention_level(metrics, recent_some_pct)
        threshold = self.contention_threshold_pct

        # Contention PRESENT
        if contention_level >= threshold:
            return True, contention_level

        # Contention PREDICTED (rising toward threshold)
        half_threshold = 0.5 * threshold
        if half_threshold <= metrics.some_avg10 < threshold and metrics.some_avg10 > metrics.some_avg60:
            return True, contention_level

        return False, contention_level

    def evaluate_cost_benefit(
        self,
        p_hit: float,
        build_dur: float,
        window_width: float,
        contention_level: float,
        speedup_frac: Optional[float] = None,
    ) -> Tuple[bool, float, float]:
        """Check if expected benefit exceeds expected cost.

        Uses measured speedup_frac if provided (Stage 9 benefit feedback),
        otherwise falls back to the prior based on contention_level.

        Args:
            p_hit: Recent hit-rate (last 10 scored predictions).
            build_dur: Median duration of project's last 10 observed builds.
            window_width: Width of the prediction window (window_hi - window_lo).
            contention_level: Current contention level in percent.
            speedup_frac: Optional measured speedup fraction from BenefitEstimator.

        Returns:
            Tuple of (passes: bool, benefit_s: float, cost_s: float).
        """
        if speedup_frac is not None:
            eff = max(0.0, float(speedup_frac))
        else:
            # speedup_frac scales from 0 at no contention to benefit_prior_max at contention >= 50%
            eff = self.benefit_prior_max * min(1.0, contention_level / 50.0)

        benefit_s = p_hit * build_dur * eff
        cost_s = (
            (1.0 - p_hit) * window_width * self.idle_cost_rate
            + p_hit * build_dur * self.other_impact_rate
        )
        return benefit_s > cost_s, benefit_s, cost_s

    def decide(
        self,
        can_arm: bool,
        p_hit: float,
        build_durations: List[float],
        window_lo: float,
        window_hi: float,
        gate_reasons: Optional[List[str]] = None,
        speedup_frac: Optional[float] = None,
    ) -> ArmDecision:
        """Evaluate all gates and return an ArmDecision.

        Order of evaluation:
            1. Confidence gate (can_arm from ConfidenceGate)
            2. Contention gate (PSI CPU check)
            3. Cost/benefit gate (benefit_s > cost_s)
            4. Holdout selector

        NOTE: No cgroup is written; no reservation is created. This is observation-only.

        Args:
            can_arm: Result from ConfidenceGate.evaluate().can_arm.
            p_hit: Recent hit-rate probability (last 10 scored predictions).
            build_durations: List of durations (seconds) from recent observed builds.
            window_lo: Prediction window lower bound (relative seconds).
            window_hi: Prediction window upper bound (relative seconds).
            gate_reasons: Reasons list from ConfidenceGate (may be None).
            speedup_frac: Optional measured speedup fraction from BenefitEstimator.

        Returns:
            ArmDecision with all gate inputs and outputs.
        """
        reasons: List[str] = list(gate_reasons or [])
        window_width = max(0.0, window_hi - window_lo)

        # Collect PSI metrics (also records into sampler history)
        metrics = self._psi_sampler.sample()
        recent_pct = self._psi_sampler.recent_some_pct
        contention_level = self.compute_contention_level(metrics, recent_pct)

        # Compute build_dur from median of recent observed builds
        if build_durations:
            sorted_durs = sorted(build_durations)
            mid = len(sorted_durs) // 2
            if len(sorted_durs) % 2 == 1:
                build_dur = sorted_durs[mid]
            else:
                build_dur = (sorted_durs[mid - 1] + sorted_durs[mid]) / 2.0
        else:
            build_dur = 0.0

        # ── Gate 1: Confidence Gate ────────────────────────────────────────────
        if self.confidence_gate_enabled:
            if not can_arm:
                return ArmDecision(
                    decision="abstain_confidence",
                    arm=False,
                    holdout=False,
                    reasons=reasons,
                    contention_level=contention_level,
                    p_hit=p_hit,
                    build_dur=build_dur,
                    window_width=window_width,
                    benefit_s=0.0,
                    cost_s=0.0,
                )
        else:
            reasons.append("confidence_gate disabled")

        # ── Gate 2: No-build-estimate check ───────────────────────────────────
        if not build_durations:
            return ArmDecision(
                decision="abstain_no_build_estimate",
                arm=False,
                holdout=False,
                reasons=reasons + ["No observed builds to estimate duration"],
                contention_level=contention_level,
                p_hit=p_hit,
                build_dur=0.0,
                window_width=window_width,
                benefit_s=0.0,
                cost_s=0.0,
            )

        # ── Gate 3: Contention Gate ────────────────────────────────────────────
        if self.contention_gate_enabled:
            contention_ok, contention_level = self.is_contention_present(metrics, recent_pct)
            if not contention_ok:
                return ArmDecision(
                    decision="abstain_no_contention",
                    arm=False,
                    holdout=False,
                    reasons=reasons + [
                        f"No contention: psi={contention_level:.1f}% < threshold={self.contention_threshold_pct:.1f}%"
                    ],
                    contention_level=contention_level,
                    p_hit=p_hit,
                    build_dur=build_dur,
                    window_width=window_width,
                    benefit_s=0.0,
                    cost_s=0.0,
                )
        else:
            reasons.append("contention_gate disabled")

        # ── Gate 4: Cost/Benefit Gate ──────────────────────────────────────────
        benefit_s = 0.0
        cost_s = 0.0
        if self.cost_gate_enabled:
            cost_ok, benefit_s, cost_s = self.evaluate_cost_benefit(
                p_hit=p_hit,
                build_dur=build_dur,
                window_width=window_width,
                contention_level=contention_level,
                speedup_frac=speedup_frac,
            )
            if not cost_ok:
                return ArmDecision(
                    decision="abstain_cost",
                    arm=False,
                    holdout=False,
                    reasons=reasons + [
                        f"Cost exceeds benefit: benefit={benefit_s:.3f}s cost={cost_s:.3f}s"
                    ],
                    contention_level=contention_level,
                    p_hit=p_hit,
                    build_dur=build_dur,
                    window_width=window_width,
                    benefit_s=benefit_s,
                    cost_s=cost_s,
                )
        else:
            reasons.append("cost_gate disabled")
            # Compute for logging even when gate is disabled
            _, benefit_s, cost_s = self.evaluate_cost_benefit(
                p_hit=p_hit,
                build_dur=build_dur,
                window_width=window_width,
                contention_level=contention_level,
                speedup_frac=speedup_frac,
            )

        # ── Gate 5: Holdout Selector ───────────────────────────────────────────
        if self.holdout_enabled and self._rng.random() < self.holdout_probability:
            return ArmDecision(
                decision="holdout",
                arm=False,
                holdout=True,
                reasons=reasons + ["Selected as holdout for benefit estimation"],
                contention_level=contention_level,
                p_hit=p_hit,
                build_dur=build_dur,
                window_width=window_width,
                benefit_s=benefit_s,
                cost_s=cost_s,
            )

        # All gates passed
        return ArmDecision(
            decision="would_arm",
            arm=True,
            holdout=False,
            reasons=reasons,
            contention_level=contention_level,
            p_hit=p_hit,
            build_dur=build_dur,
            window_width=window_width,
            benefit_s=benefit_s,
            cost_s=cost_s,
        )
