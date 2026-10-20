"""Self-measured benefit estimator and priority feedback control.

Stage Mapping: Stage 9 (Self-Measured Benefit Estimator & Priority Feedback Control).
Provides:
    - BenefitEstimator: Matches reserved and holdout build runs across contention bins,
      evaluates speedup and statistical significance (Mann-Whitney U, bootstrap CI, Stouffer Z),
      decays weight or disables arming when not significant, and issues probes to detect
      re-emerging benefit.
"""

import json
import logging
import math
import random
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from precompile.stats import (
    bootstrap_relative_effect_ci,
    mann_whitney_u,
    median,
    percentile,
    stouffer_z_combine,
)
from precompile.store import DataStore

logger = logging.getLogger("precompile.benefit")

CONTENTION_BINS = [
    ("low", 0.0, 10.0),
    ("mild", 10.0, 30.0),
    ("moderate", 30.0, 60.0),
    ("high", 60.0, 100.0),
]


def classify_contention_bin(contention_level: float) -> str:
    """Classify CPU stall contention level [0, 100] into one of 4 discrete bins."""
    lvl = max(0.0, min(100.0, float(contention_level)))
    if lvl < 10.0:
        return "low"
    elif lvl < 30.0:
        return "mild"
    elif lvl < 60.0:
        return "moderate"
    else:
        return "high"


class BenefitEstimator:
    """Closed-loop benefit estimator and priority feedback controller.

    Evaluates whether priority boost achieves statistically significant build speedup
    compared to unboosted holdouts under equivalent contention.
    """

    def __init__(
        self,
        store: DataStore,
        configured_weight: int = 500,
        min_bin_n: int = 5,
        min_total_n: int = 8,
        eval_every: int = 10,
        effect_min: float = 0.05,
        p_val_alpha: float = 0.05,
        probe_probability: float = 0.20,
        clock: Callable[[], float] = time.monotonic,
        contention_gate_enabled: bool = True,
        contention_threshold_pct: float = 10.0,
    ) -> None:
        """Initialize BenefitEstimator.

        Args:
            store: DataStore instance for reading builds and persisting state.
            configured_weight: Target/configured elevated CPU weight (e.g. 500).
            min_bin_n: Minimum samples per arm in a bin to evaluate bin effect (default 5).
            min_total_n: Minimum total builds per arm before evaluating (default 8).
            eval_every: Evaluate every N new paired builds (default 10).
            effect_min: Minimum relative speedup to be considered beneficial (default 5%).
            p_val_alpha: Significance threshold (default 0.05).
            probe_probability: Probability of issuing a probe when arming is disabled (default 0.20).
            clock: Monotonic clock provider.
            contention_gate_enabled: Whether PSI contention gate is active (restricting decision to bins >= threshold).
            contention_threshold_pct: Contention threshold percentage (default 10.0%).
        """
        self.store = store
        self.configured_weight = configured_weight
        self.min_bin_n = min_bin_n
        self.min_total_n = min_total_n
        self.eval_every = eval_every
        self.effect_min = effect_min
        self.p_val_alpha = p_val_alpha
        self.probe_probability = probe_probability
        self.clock = clock
        self.contention_gate_enabled = contention_gate_enabled
        self.contention_threshold_pct = contention_threshold_pct

    def get_or_create_state(self, project_id: str) -> Dict[str, Any]:
        """Fetch current benefit state from store, or create initial default state."""
        st = self.store.get_benefit_state(project_id)
        if st is not None:
            return st

        # Initial state: full configured weight, arming enabled
        self.store.set_benefit_state(
            project_id=project_id,
            weight=self.configured_weight,
            arming_enabled=True,
            status="active",
            n_reserved=0,
            n_holdout=0,
        )
        return self.store.get_benefit_state(project_id)  # type: ignore

    def current_weight(self, project_id: str) -> int:
        """Return the current dynamic cgroup weight for this project."""
        st = self.get_or_create_state(project_id)
        return int(st["weight"])

    def is_arming_enabled(self, project_id: str) -> bool:
        """Check if arming is enabled (or probe eligible) for this project."""
        st = self.get_or_create_state(project_id)
        return bool(st["arming_enabled"])

    def should_probe(self, project_id: str) -> bool:
        """Determine if a probe should be issued while arming is disabled."""
        st = self.get_or_create_state(project_id)
        if st["arming_enabled"]:
            return False
        return random.random() < self.probe_probability

    def expected_speedup_frac(self, project_id: str, contention_level: float) -> Optional[float]:
        """Return measured expected speedup fraction for the given contention level.

        Returns None if no statistically significant benefit has been established yet.
        """
        evals = self.store.list_benefit_evals(project_id, limit=1)
        if not evals:
            return None

        latest = evals[0]
        if latest["action"] != "keep" or latest["overall_effect"] < self.effect_min:
            return None

        # Check bin-specific effect if available
        bin_name = classify_contention_bin(contention_level)
        try:
            per_bin = json.loads(latest["per_bin_json"])
            if bin_name in per_bin and per_bin[bin_name].get("effect") is not None:
                b_eff = float(per_bin[bin_name]["effect"])
                if b_eff > 0:
                    return b_eff
        except Exception:
            pass

        return float(latest["overall_effect"])

    def maybe_evaluate(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Check if enough new builds have accumulated, and run evaluation if so.

        Called by the daemon after recording a build.
        """
        st = self.get_or_create_state(project_id)

        # Query all reserved and holdout builds for this project
        reserved_builds = self.store.builds_by_condition(project_id, "reserved")
        holdout_builds = self.store.builds_by_condition(project_id, "holdout")

        nr = len(reserved_builds)
        nh = len(holdout_builds)

        # Check total sample threshold
        if nr < self.min_total_n or nh < self.min_total_n:
            return None

        # Check cadence: evaluate every N new combined builds
        last_eval_n = st["n_reserved"] + st["n_holdout"]
        curr_total_n = nr + nh
        if last_eval_n > 0 and (curr_total_n - last_eval_n) < self.eval_every:
            return None

        return self.evaluate(project_id, reserved_builds, holdout_builds)

    def evaluate(
        self,
        project_id: str,
        reserved_builds: Optional[List[Dict[str, Any]]] = None,
        holdout_builds: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Execute full statistical benefit evaluation and update feedback state machine."""
        if reserved_builds is None:
            reserved_builds = self.store.builds_by_condition(project_id, "reserved")
        if holdout_builds is None:
            holdout_builds = self.store.builds_by_condition(project_id, "holdout")

        nr = len(reserved_builds)
        nh = len(holdout_builds)

        # Stratify builds into 4 contention bins
        r_by_bin: Dict[str, List[float]] = {b[0]: [] for b in CONTENTION_BINS}
        h_by_bin: Dict[str, List[float]] = {b[0]: [] for b in CONTENTION_BINS}

        for b in reserved_builds:
            c_lvl = getattr(b, "contention_level", None)
            if c_lvl is None and isinstance(b, dict):
                c_lvl = b.get("contention_level", 0.0)
            c_bin = classify_contention_bin(c_lvl if c_lvl is not None else 0.0)

            dur = getattr(b, "duration_s", None)
            if dur is None and isinstance(b, dict):
                dur = b.get("duration_s", 0.0)
            r_by_bin[c_bin].append(float(dur if dur is not None else 0.0))

        for b in holdout_builds:
            c_lvl = getattr(b, "contention_level", None)
            if c_lvl is None and isinstance(b, dict):
                c_lvl = b.get("contention_level", 0.0)
            c_bin = classify_contention_bin(c_lvl if c_lvl is not None else 0.0)

            dur = getattr(b, "duration_s", None)
            if dur is None and isinstance(b, dict):
                dur = b.get("duration_s", 0.0)
            h_by_bin[c_bin].append(float(dur if dur is not None else 0.0))

        # Analyze per-bin effects, CIs, and p-values
        per_bin_stats: Dict[str, Any] = {}
        all_eligible_bins: List[str] = []

        for b_name, b_lo, b_hi in CONTENTION_BINS:
            r_vals = r_by_bin[b_name]
            h_vals = h_by_bin[b_name]
            count_r = len(r_vals)
            count_h = len(h_vals)

            if count_r >= self.min_bin_n and count_h >= self.min_bin_n:
                med_r = median(r_vals)
                med_h = median(h_vals)
                eff = 1.0 - (med_r / med_h) if med_h > 0 else 0.0
                _, _, _, p_val = mann_whitney_u(r_vals, h_vals)
                _, b_ci_low, b_ci_high = bootstrap_relative_effect_ci(
                    {b_name: r_vals},
                    {b_name: h_vals},
                    n_resamples=1000,
                    alpha=self.p_val_alpha,
                    seed=42,
                    eligible_bins=[b_name],
                )

                per_bin_stats[b_name] = {
                    "n_reserved": count_r,
                    "n_holdout": count_h,
                    "median_reserved": med_r,
                    "median_holdout": med_h,
                    "effect": eff,
                    "ci_low": b_ci_low,
                    "ci_high": b_ci_high,
                    "p_value": p_val,
                }
                all_eligible_bins.append(b_name)
            else:
                per_bin_stats[b_name] = {
                    "n_reserved": count_r,
                    "n_holdout": count_h,
                    "median_reserved": None,
                    "median_holdout": None,
                    "effect": None,
                    "ci_low": None,
                    "ci_high": None,
                    "p_value": None,
                }

        # Determine decision set bins:
        # When contention gate is enabled (normal case), only bins with contention >= contention_threshold_pct count
        # (mild, moderate, high where b_lo >= threshold). When disabled, all eligible bins count.
        if self.contention_gate_enabled:
            decision_bins = [
                b for b in all_eligible_bins
                if any(b_name == b and b_lo >= self.contention_threshold_pct for b_name, b_lo, _ in CONTENTION_BINS)
            ]
        else:
            decision_bins = list(all_eligible_bins)

        # Decision set effect, CI, and combined p-value
        if decision_bins:
            dec_effect, dec_ci_low, dec_ci_high = bootstrap_relative_effect_ci(
                r_by_bin,
                h_by_bin,
                n_resamples=1000,
                alpha=self.p_val_alpha,
                seed=42,
                eligible_bins=decision_bins,
            )
            dec_p = stouffer_z_combine([
                (per_bin_stats[b]["p_value"], float(len(r_by_bin[b]) + len(h_by_bin[b])))
                for b in decision_bins
            ])
        else:
            dec_effect, dec_ci_low, dec_ci_high, dec_p = 0.0, 0.0, 0.0, 1.0

        # Pooled effect and CI across ALL eligible bins (for explicit pooled reporting)
        if all_eligible_bins:
            pooled_effect, pooled_ci_low, pooled_ci_high = bootstrap_relative_effect_ci(
                r_by_bin,
                h_by_bin,
                n_resamples=1000,
                alpha=self.p_val_alpha,
                seed=42,
                eligible_bins=all_eligible_bins,
            )
            pooled_p = stouffer_z_combine([
                (per_bin_stats[b]["p_value"], float(len(r_by_bin[b]) + len(h_by_bin[b])))
                for b in all_eligible_bins
            ])
        else:
            pooled_effect, pooled_ci_low, pooled_ci_high, pooled_p = 0.0, 0.0, 0.0, 1.0

        per_bin_stats["pooled"] = {
            "effect": pooled_effect,
            "ci_low": pooled_ci_low,
            "ci_high": pooled_ci_high,
            "p_value": pooled_p,
            "eligible_bins": all_eligible_bins,
        }
        per_bin_stats["decision_set"] = {
            "bins": decision_bins,
            "effect": dec_effect,
            "ci_low": dec_ci_low,
            "ci_high": dec_ci_high,
            "p_value": dec_p,
        }

        # State machine transition logic
        st = self.get_or_create_state(project_id)
        current_w = int(st["weight"])
        current_arming = bool(st["arming_enabled"])
        current_status = str(st["status"])

        # Guard: controller takes NO action (no decay, no disable) if sample requirements are not met
        has_min_total = (nr >= self.min_total_n and nh >= self.min_total_n)
        has_decision_evidence = bool(decision_bins)

        if not (has_min_total and has_decision_evidence):
            # Insufficient evidence in the domain that matters -> action is 'none'
            action = "none"
            weight_after = current_w
            arming_after = current_arming
            status_after = current_status
        else:
            is_significant = (dec_p < self.p_val_alpha) and (dec_effect >= self.effect_min)

            if is_significant:
                # Significant benefit established in decision domain:
                # Keep arming enabled; step weight up if previously decayed
                action = "keep"
                weight_after = min(self.configured_weight, current_w * 2 if current_w < self.configured_weight else current_w)
                arming_after = True
                status_after = "active"
            else:
                # Benefit not statistically significant in decision domain:
                # Decay weight (500 -> 250 -> 125 -> 100)
                if current_w > 100:
                    action = "decay"
                    weight_after = max(100, current_w // 2)
                    arming_after = True
                    status_after = "decayed"
                else:
                    # Already at minimum baseline weight 100: disable arming
                    action = "disable"
                    weight_after = 100
                    arming_after = False
                    status_after = "disabled"

        # Record evaluation in store (decision-set driven overall effect, CI, p)
        eval_id = self.store.record_benefit_eval(
            project_id=project_id,
            evaluated_at=time.time(),
            n_reserved=nr,
            n_holdout=nh,
            per_bin_json=json.dumps(per_bin_stats),
            overall_effect=dec_effect,
            ci_low=dec_ci_low,
            ci_high=dec_ci_high,
            p_value=dec_p,
            action=action,
            weight_after=weight_after,
            arming_after=arming_after,
        )

        # Update benefit state
        self.store.set_benefit_state(
            project_id=project_id,
            weight=weight_after,
            arming_enabled=arming_after,
            status=status_after,
            last_effect=dec_effect,
            last_p_value=dec_p,
            n_reserved=nr,
            n_holdout=nh,
            last_eval_at=time.time(),
        )

        result = {
            "eval_id": eval_id,
            "project_id": project_id,
            "n_reserved": nr,
            "n_holdout": nh,
            "overall_effect": dec_effect,
            "ci_low": dec_ci_low,
            "ci_high": dec_ci_high,
            "p_value": dec_p,
            "pooled_effect": pooled_effect,
            "pooled_ci_low": pooled_ci_low,
            "pooled_ci_high": pooled_ci_high,
            "pooled_p_value": pooled_p,
            "decision_bins": decision_bins,
            "action": action,
            "weight_after": weight_after,
            "arming_after": arming_after,
            "per_bin": per_bin_stats,
        }

        # Build detailed per-bin string for logging
        bin_desc = []
        for b_name, _, _ in CONTENTION_BINS:
            bs = per_bin_stats[b_name]
            if bs["effect"] is not None:
                bin_desc.append(
                    f"{b_name}(nR={bs['n_reserved']}, nH={bs['n_holdout']}, eff={bs['effect']*100:+.1f}%, "
                    f"95%CI=[{bs['ci_low']*100:+.1f}%, {bs['ci_high']*100:+.1f}%], p={bs['p_value']:.4f})"
                )
            else:
                bin_desc.append(f"{b_name}(nR={bs['n_reserved']}, nH={bs['n_holdout']}, ineligible)")

        logger.info(
            "[benefit] %s eval: per-bin: [%s] | decision(bins=%s, eff=%.3f, 95%% CI [%.3f, %.3f], p=%.4f) | "
            "pooled(eff=%.3f, 95%% CI [%.3f, %.3f], p=%.4f) -> action=%s weight=%d arming=%s",
            project_id,
            "; ".join(bin_desc),
            decision_bins,
            dec_effect,
            dec_ci_low,
            dec_ci_high,
            dec_p,
            pooled_effect,
            pooled_ci_low,
            pooled_ci_high,
            pooled_p,
            action,
            weight_after,
            arming_after,
        )

        return result
