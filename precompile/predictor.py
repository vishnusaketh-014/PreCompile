"""Event-conditioned latency learning engine and prediction window calculator.

Stage Mapping: Stage 3 (Event-Conditioned Latency Learning) & Stage 4 (Prediction Window).
Maintains running EWMA mean and variance per (project, file_class, tool_profile), applies
outlier clipping with a variance floor, filters by correlation window, and computes
reservation windows.

Exact Formulas:
    mean_n = (1 - alpha) * mean_(n-1) + alpha * x_n
    var_n  = (1 - alpha) * (var_(n-1) + alpha * (x_n - mean_(n-1))^2)
    sigma_n = sqrt(var_n)
    window = [max(0, mean - k * sigma - m), mean + k * sigma + m]

Design Decision - Outlier Clipping Sigma Floor:
    When n >= clip_min_samples (default 5), samples are clipped to [mean - 3*s, mean + 3*s]
    where s = max(sigma, clip_sigma_floor_s) (default floor = 0.1s).
    This floor is a deliberate deviation from the strict text of the spec. Without a sigma
    floor, a series of identical delays collapses sigma to 0, which would permanently lock
    the clipping bounds at [mean, mean] and prevent the model from adapting to legitimate
    step-changes in build latency (e.g. build tool updates or workflow changes).
"""

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from precompile.config import PredictorConfig
from precompile.gates import ConfidenceGate, GateResult
from precompile.store import DataStore


@dataclass
class UpdateResult:
    """Result of updating a LatencyModel with a delay sample."""
    accepted: bool
    clipped: bool
    x_used: Optional[float] = None
    reason: Optional[str] = None


@dataclass
class LatencyModel:
    """Running EWMA latency model maintaining n, mean, and var."""
    n: int = 0
    mean: float = 0.0
    var: float = 0.0

    @property
    def sigma(self) -> float:
        """Standard deviation sqrt(var)."""
        return math.sqrt(max(0.0, self.var))

    @property
    def cv(self) -> Optional[float]:
        """Coefficient of variation (sigma / mean).

        Returns None when mean <= 1e-9 to prevent division by zero.
        """
        if self.mean <= 1e-9:
            return None
        return self.sigma / self.mean

    # Backward compatibility properties
    @property
    def sample_count(self) -> int:
        return self.n

    @property
    def variance(self) -> float:
        return self.var

    def update(
        self,
        x: float,
        config: Optional[PredictorConfig] = None,
        alpha: Optional[float] = None,
        clip_sigma: Optional[float] = None,
        clip_min_samples: Optional[int] = None,
        clip_sigma_floor_s: Optional[float] = None,
        correlation_window_s: Optional[float] = None,
        negative_delay_tolerance_s: Optional[float] = None,
    ) -> UpdateResult:
        """Update EWMA mean and variance with a new sample x.

        Rules applied in order:
            a. x > correlation_window_s: reject, do not update model.
            b. x < -negative_delay_tolerance_s: reject.
               If -tolerance <= x < 0, clamp x to 0.
            c. Outlier clipping applies only when n >= clip_min_samples:
               clip x to [mean - 3*s, mean + 3*s] where s = max(sigma, clip_sigma_floor_s).

        Args:
            x: Raw delay sample in seconds.
            config: PredictorConfig containing hyperparameters.
            alpha: Optional override for EWMA smoothing factor.
            clip_sigma: Optional override for outlier clipping sigma multiplier.
            clip_min_samples: Optional override for min samples before clipping applies.
            clip_sigma_floor_s: Optional override for clipping floor.
            correlation_window_s: Optional override for correlation window ceiling.
            negative_delay_tolerance_s: Optional override for negative delay tolerance.

        Returns:
            UpdateResult(accepted, clipped, x_used, reason).
        """
        cfg = config if config is not None else PredictorConfig()
        _alpha = alpha if alpha is not None else cfg.alpha
        _clip_sigma = clip_sigma if clip_sigma is not None else cfg.clip_sigma
        _clip_min_samples = clip_min_samples if clip_min_samples is not None else cfg.clip_min_samples
        _clip_floor = clip_sigma_floor_s if clip_sigma_floor_s is not None else cfg.clip_sigma_floor_s
        _corr_win = correlation_window_s if correlation_window_s is not None else cfg.correlation_window_s
        _neg_tol = (
            negative_delay_tolerance_s
            if negative_delay_tolerance_s is not None
            else cfg.negative_delay_tolerance_s
        )

        # Rule a: correlation window ceiling
        if x > _corr_win:
            return UpdateResult(
                accepted=False,
                clipped=False,
                x_used=None,
                reason=f"Delay {x:.4f}s > correlation window {_corr_win:.1f}s",
            )

        # Rule b: negative delay validation and clamp
        if x < -_neg_tol:
            return UpdateResult(
                accepted=False,
                clipped=False,
                x_used=None,
                reason=f"Negative delay {x:.4f}s exceeds tolerance -{_neg_tol:.4f}s",
            )
        if -_neg_tol <= x < 0.0:
            x = 0.0

        # Rule c: outlier clipping (only when n >= clip_min_samples)
        clipped = False
        x_used = x
        if self.n >= _clip_min_samples:
            s = max(self.sigma, _clip_floor)
            lo = self.mean - _clip_sigma * s
            hi = self.mean + _clip_sigma * s
            if x < lo:
                x_used = lo
                clipped = True
            elif x > hi:
                x_used = hi
                clipped = True

        # EWMA state update
        if self.n == 0:
            self.mean = x_used
            self.var = 0.0
            self.n = 1
        else:
            old_mean = self.mean
            # Must compute var with OLD mean before updating mean
            new_var = (1.0 - _alpha) * (self.var + _alpha * ((x_used - old_mean) ** 2))
            new_mean = (1.0 - _alpha) * old_mean + _alpha * x_used
            self.var = new_var
            self.mean = new_mean
            self.n += 1

        return UpdateResult(accepted=True, clipped=clipped, x_used=x_used)


# Backward compatibility aliases
ModelStats = LatencyModel


@dataclass
class PredictionWindow:
    """Calculated start and end offsets relative to save timestamp."""
    window_start_offset: float
    window_end_offset: float
    mean: float
    sigma: float


@dataclass
class Prediction:
    """Prediction outcome and window details generated upon save."""
    prediction_id: int
    key: Tuple[str, str, str]
    t_save_monotonic: float
    window_lo: float
    window_hi: float
    can_arm: bool
    gate_result: GateResult

    @property
    def window_start(self) -> float:
        return self.window_lo

    @property
    def window_end(self) -> float:
        return self.window_hi


@dataclass
class _PendingSaveState:
    """Internal pending save state awaiting a matching process spawn."""
    project_id: str
    file_class: str
    tool_profile: str
    t_save_monotonic: float
    t_save_wall: float
    prediction_id: Optional[int]
    window_lo: Optional[float]
    window_hi: Optional[float]
    spawn_handled: bool = False


def prediction_window(
    model: LatencyModel,
    k: float = 2.0,
    margin_m: float = 0.3,
) -> Tuple[float, float]:
    """Calculate the prediction window [lo, hi] measured from save time.

    Formula:
        lo = max(0, mean - k * sigma - m)
        hi = mean + k * sigma + m
    """
    lo = max(0.0, model.mean - k * model.sigma - margin_m)
    hi = model.mean + k * model.sigma + margin_m
    return lo, hi


def window_abs(
    t_save_monotonic: float,
    model: LatencyModel,
    k: float = 2.0,
    margin_m: float = 0.3,
) -> Tuple[float, float]:
    """Calculate absolute monotonic timestamps for the prediction window."""
    lo, hi = prediction_window(model, k=k, margin_m=margin_m)
    return t_save_monotonic + lo, t_save_monotonic + hi


class LatencyPredictor:
    """Learns event-to-process latency using EWMA and computes reservation windows."""

    def __init__(
        self,
        store: Optional[DataStore] = None,
        config: Optional[PredictorConfig] = None,
        clock: Callable[[], float] = time.monotonic,
        # Backward compatibility kwargs
        alpha: Optional[float] = None,
        k: Optional[float] = None,
        m: Optional[float] = None,
        correlation_window: Optional[float] = None,
        outlier_clip_sigma: Optional[float] = None,
    ) -> None:
        """Initialize LatencyPredictor.

        Args:
            store: DataStore instance. If None, an in-memory store is created.
            config: PredictorConfig instance.
            clock: Monotonic clock provider (injectable for testing).
        """
        self.config = config if config is not None else PredictorConfig(
            alpha=alpha if alpha is not None else 0.2,
            k=k if k is not None else 2.0,
            margin_m=m if m is not None else 0.3,
            correlation_window_s=correlation_window if correlation_window is not None else 30.0,
            clip_sigma=outlier_clip_sigma if outlier_clip_sigma is not None else 3.0,
        )
        self.store = store if store is not None else DataStore(":memory:")
        self.clock = clock
        self.gate = ConfidenceGate(self.config)
        self._lock = threading.RLock()
        self._models: Dict[Tuple[str, str, str], LatencyModel] = {}
        self._pending_saves: Dict[str, _PendingSaveState] = {}

    def _get_or_load_model(self, key: Tuple[str, str, str]) -> LatencyModel:
        """Lazily load EWMA model state from store or create new."""
        with self._lock:
            if key not in self._models:
                saved = self.store.load_model_state(key)
                if saved is not None:
                    n, mean, var = saved
                    model = LatencyModel(n=n, mean=mean, var=var)
                else:
                    model = LatencyModel()
                self._models[key] = model
            return self._models[key]

    def on_save(self, save_event: Any) -> Optional[Prediction]:
        """Handle a save event from FileMonitor.

        Ignores file classes not in learned_file_classes.
        Evaluates ConfidenceGate: if can_predict, records a shadow prediction in store
        and returns Prediction.
        Tracks pending save per project (newer saves replace earlier unscored ones).

        Args:
            save_event: SaveEvent instance with project_id, file_class, t_save_monotonic, t_save_wall.

        Returns:
            Prediction dataclass or None.
        """
        file_class = getattr(save_event, "file_class", "")
        if file_class not in self.config.learned_file_classes:
            return None

        project_id = getattr(save_event, "project_id", "")
        t_save_mono = getattr(save_event, "t_save_monotonic", self.clock())
        t_save_wall = getattr(save_event, "t_save_wall", time.time())

        with self._lock:
            proj = self.store.get_project(project_id)
            tool_profile = proj.tool_profile_name if proj is not None else "default"
            key = (project_id, file_class, tool_profile)
            model = self._get_or_load_model(key)

            hit_stats = self.store.recent_hit_rate(
                project_id, file_class, tool_profile, last=self.config.hit_rate_last
            )
            gate_res = self.gate.evaluate(model, hit_stats)

            pred: Optional[Prediction] = None
            pred_id: Optional[int] = None
            lo: Optional[float] = None
            hi: Optional[float] = None

            if gate_res.can_predict:
                lo, hi = prediction_window(model, k=self.config.k, margin_m=self.config.margin_m)
                pred_id = self.store.record_prediction(
                    project_id=project_id,
                    file_class=file_class,
                    tool_profile=tool_profile,
                    t_save_wall=t_save_wall,
                    window_lo=lo,
                    window_hi=hi,
                    armed=False,
                    holdout=False,
                )
                pred = Prediction(
                    prediction_id=pred_id,
                    key=key,
                    t_save_monotonic=t_save_mono,
                    window_lo=lo,
                    window_hi=hi,
                    can_arm=gate_res.can_arm,
                    gate_result=gate_res,
                )

            # Store pending save. A newer save replaces pending one;
            # the replaced save's prediction remains unscored in store.
            self._pending_saves[project_id] = _PendingSaveState(
                project_id=project_id,
                file_class=file_class,
                tool_profile=tool_profile,
                t_save_monotonic=t_save_mono,
                t_save_wall=t_save_wall,
                prediction_id=pred_id,
                window_lo=lo,
                window_hi=hi,
                spawn_handled=False,
            )

            return pred

    def on_process_spawn(
        self,
        project_id: str,
        t_start_monotonic: float,
    ) -> Optional[UpdateResult]:
        """Handle a matching process spawn event from ProcessWatcher.

        Only the FIRST matching spawn after a save counts; subsequent spawns
        for the same save are ignored.
        Calculates delay = t_start_monotonic - t_save_monotonic.
        Updates model and scores any open prediction.

        Args:
            project_id: Project identifier.
            t_start_monotonic: Estimated process start time.

        Returns:
            UpdateResult or None.
        """
        with self._lock:
            if project_id not in self._pending_saves:
                return None

            pending = self._pending_saves[project_id]
            if pending.spawn_handled:
                return None
            pending.spawn_handled = True

            delay = t_start_monotonic - pending.t_save_monotonic
            key = (pending.project_id, pending.file_class, pending.tool_profile)
            model = self._get_or_load_model(key)

            if delay > self.config.correlation_window_s:
                # Treat as no_build: record sample as rejected, score prediction as no_build
                self.store.record_sample(
                    project_id=pending.project_id,
                    file_class=pending.file_class,
                    tool_profile=pending.tool_profile,
                    delay_s=delay,
                    accepted=False,
                    t_save_wall=pending.t_save_wall,
                )
                if pending.prediction_id is not None:
                    self.store.score_prediction(
                        pending.prediction_id,
                        outcome="no_build",
                        observed_delay_s=delay,
                    )
                del self._pending_saves[project_id]
                return UpdateResult(
                    accepted=False,
                    clipped=False,
                    x_used=None,
                    reason=f"Delay {delay:.4f}s > correlation window {self.config.correlation_window_s:.1f}s",
                )

            update_res = model.update(delay, self.config)
            self.store.record_sample(
                project_id=pending.project_id,
                file_class=pending.file_class,
                tool_profile=pending.tool_profile,
                delay_s=delay,
                accepted=update_res.accepted,
                t_save_wall=pending.t_save_wall,
            )
            if update_res.accepted:
                self.store.save_model_state(key, model.n, model.mean, model.var)

            if pending.prediction_id is not None:
                if pending.window_lo is not None and pending.window_hi is not None:
                    is_hit = (pending.window_lo <= delay <= pending.window_hi)
                    outcome = "hit" if is_hit else "miss"
                else:
                    outcome = "miss"
                self.store.score_prediction(
                    pending.prediction_id,
                    outcome=outcome,
                    observed_delay_s=delay,
                )

            del self._pending_saves[project_id]
            return update_res

    def expire_pending(self, now: Optional[float] = None) -> List[int]:
        """Expire pending saves older than correlation_window_s with no matching spawn.

        Scores unfulfilled predictions as 'no_build' (counted as miss).

        Args:
            now: Current monotonic timestamp. Defaults to self.clock().

        Returns:
            List of expired prediction IDs.
        """
        current_time = now if now is not None else self.clock()
        expired_ids: List[int] = []

        with self._lock:
            for proj_id in list(self._pending_saves.keys()):
                pending = self._pending_saves[proj_id]
                if current_time - pending.t_save_monotonic > self.config.correlation_window_s:
                    if not pending.spawn_handled and pending.prediction_id is not None:
                        self.store.score_prediction(
                            pending.prediction_id,
                            outcome="no_build",
                            observed_delay_s=None,
                        )
                        expired_ids.append(pending.prediction_id)
                    del self._pending_saves[proj_id]

        return expired_ids

    def describe(self, key: Tuple[str, str, str]) -> Dict[str, Any]:
        """Return explainable status dictionary for CLI status reporting.

        Args:
            key: Model tuple (project_id, file_class, tool_profile).

        Returns:
            Dictionary with n, mean, sigma, cv, window, gate_result, and recent_hit_rate.
        """
        with self._lock:
            project_id, file_class, tool_profile = key
            model = self._get_or_load_model(key)
            hit_stats = self.store.recent_hit_rate(
                project_id, file_class, tool_profile, last=self.config.hit_rate_last
            )
            gate_res = self.gate.evaluate(model, hit_stats)
            hits, scored_total, rate = hit_stats
            lo, hi = prediction_window(model, k=self.config.k, margin_m=self.config.margin_m)

            return {
                "project_id": project_id,
                "file_class": file_class,
                "tool_profile": tool_profile,
                "n": model.n,
                "mean": model.mean,
                "var": model.var,
                "sigma": model.sigma,
                "cv": model.cv,
                "window": (lo, hi),
                "gate_result": {
                    "can_predict": gate_res.can_predict,
                    "can_arm": gate_res.can_arm,
                    "reasons": list(gate_res.reasons),
                },
                "recent_hit_rate": {
                    "hits": hits,
                    "scored_total": scored_total,
                    "rate": rate,
                },
            }

    # Backward compatibility API methods

    def get_stats(
        self,
        project_id: str,
        file_class: str,
        tool_profile: str,
    ) -> Optional[LatencyModel]:
        """Retrieve current statistical model state for a key."""
        key = (project_id, file_class, tool_profile)
        model = self._get_or_load_model(key)
        return model if model.n > 0 else None

    def compute_window(
        self,
        project_id: str,
        file_class: str,
        tool_profile: str,
    ) -> Optional[PredictionWindow]:
        """Calculate the prediction window [mean - k*sigma - m, mean + k*sigma + m]."""
        key = (project_id, file_class, tool_profile)
        model = self._get_or_load_model(key)
        if model.n == 0:
            return None
        lo, hi = prediction_window(model, k=self.config.k, margin_m=self.config.margin_m)
        return PredictionWindow(
            window_start_offset=lo,
            window_end_offset=hi,
            mean=model.mean,
            sigma=model.sigma,
        )

    def update(
        self,
        project_id: str,
        file_class: str,
        tool_profile: str,
        delay_seconds: float,
    ) -> Optional[LatencyModel]:
        """Update EWMA model directly without a save event (backward compat)."""
        key = (project_id, file_class, tool_profile)
        model = self._get_or_load_model(key)
        res = model.update(delay_seconds, self.config)
        if not res.accepted:
            return None
        self.store.save_model_state(key, model.n, model.mean, model.var)
        return model
