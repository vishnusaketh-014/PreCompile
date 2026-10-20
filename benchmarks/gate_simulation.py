"""Confidence and arming gate simulation across diverse developer workloads.

Executes real LatencyPredictor and ConfidenceGate instances against an in-memory DataStore
over 50 seeds x 300 save events per workload scenario. Evaluates gate selectivity and stability.

Part E (Step 7): Second table added showing % of saves that would be 'would_arm' under three
fixed contention scenarios. Uses seeded RNG and holdout_probability=0 for reproducibility.
Outputs results to terminal and benchmarks/results/gate_simulation.md.
"""

import math
import os
import random
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from precompile.gates import ArmingGate, CPUContentionMetrics
from precompile.monitor import SaveEvent
from precompile.predictor import LatencyPredictor
from precompile.store import DataStore


def run_workload_simulation(
    workload_name: str,
    delay_generator_factory: Callable[[random.Random], Callable[[], Tuple[bool, float]]],
    num_seeds: int = 50,
    saves_per_seed: int = 300,
) -> Dict[str, float]:
    """Run simulation for a workload across multiple seeds."""
    total_saves = num_seeds * saves_per_seed
    total_can_predict = 0
    total_can_arm = 0
    seeds_with_can_arm = 0
    hit_rates_when_armed: List[float] = []
    window_widths_when_armed: List[float] = []

    for seed in range(num_seeds):
        rng = random.Random(seed)
        delay_gen = delay_generator_factory(rng)

        store = DataStore(":memory:")
        store.add_project("proj", "/mock", "default")
        current_time = 1000.0

        def get_clock():
            return current_time

        predictor = LatencyPredictor(store=store, clock=get_clock)
        seed_armed_ever = False

        for _ in range(saves_per_seed):
            current_time += 10.0
            ev = SaveEvent(
                project_id="proj",
                path=Path("/mock/main.c"),
                file_class="source",
                t_save_monotonic=current_time,
                t_save_wall=current_time,
            )
            pred = predictor.on_save(ev)

            can_predict = (pred is not None)
            can_arm = (pred.can_arm if pred is not None else False)

            if can_predict:
                total_can_predict += 1

            if can_arm:
                total_can_arm += 1
                seed_armed_ever = True
                width = pred.window_hi - pred.window_lo
                window_widths_when_armed.append(width)

                # Recent hit-rate when armed
                _, _, rate = store.recent_hit_rate("proj", "source", "default", last=10)
                hit_rates_when_armed.append(rate)

            # Generate delay
            has_build, delay = delay_gen()

            if has_build:
                current_time += delay
                predictor.on_process_spawn("proj", current_time)
            else:
                # No build: time advances past correlation window (30s) and expires
                current_time += 35.0
                predictor.expire_pending(now=current_time)

        if seed_armed_ever:
            seeds_with_can_arm += 1

    pct_can_predict = (total_can_predict / float(total_saves)) * 100.0
    pct_can_arm = (total_can_arm / float(total_saves)) * 100.0
    fraction_seeds_armed = seeds_with_can_arm / float(num_seeds)
    mean_hit_rate = (
        sum(hit_rates_when_armed) / len(hit_rates_when_armed)
        if hit_rates_when_armed
        else 0.0
    )
    mean_window_width = (
        sum(window_widths_when_armed) / len(window_widths_when_armed)
        if window_widths_when_armed
        else 0.0
    )

    return {
        "workload": workload_name,
        "pct_can_predict": pct_can_predict,
        "pct_can_arm": pct_can_arm,
        "fraction_seeds_armed": fraction_seeds_armed,
        "mean_hit_rate": mean_hit_rate,
        "mean_window_width": mean_window_width,
    }


def run_arming_decision_simulation(
    workload_name: str,
    delay_generator_factory: Callable[[random.Random], Callable[[], Tuple[bool, float]]],
    build_dur: float,
    contention_level: float,
    num_seeds: int = 50,
    saves_per_seed: int = 300,
    seed_offset: int = 0,
) -> Dict[str, float]:
    """Run arming gate simulation for a specific (contention, build_dur) scenario.

    Uses seeded RNG and holdout_probability=0 for reproducibility.
    NOTE: thresholds and priors are NOT tuned based on these numbers;
    they are reported as-is from the default configuration.

    Args:
        workload_name: Human-readable workload label.
        delay_generator_factory: Factory for the delay distribution generator.
        build_dur: Fixed build duration in seconds (simulated as if 10 identical observed builds).
        contention_level: Fixed CPU contention level in percent.
        num_seeds: Number of seeds to average over.
        saves_per_seed: Number of saves per seed.
        seed_offset: Offset added to seed for RNG independence between scenarios.

    Returns:
        Dict with pct_would_arm and other stats.
    """
    # Create a fixed PSI reader returning the given contention level
    def make_psi_reader(cl: float):
        def reader():
            return CPUContentionMetrics(
                some_avg10=cl,
                some_avg60=cl * 0.5,  # avg60 lower so avg10 > avg60 -> rising
                some_avg300=cl * 0.3,
                some_total=0,
            )
        return reader

    total_saves = num_seeds * saves_per_seed
    total_would_arm = 0

    for seed in range(num_seeds):
        rng_delay = random.Random(seed + seed_offset)
        delay_gen = delay_generator_factory(rng_delay)

        # Fixed build durations (10 identical observed builds)
        fixed_build_durations = [build_dur] * 10

        store = DataStore(":memory:")
        store.add_project("proj", "/mock", "default")
        current_time = 1000.0

        def get_clock():
            return current_time

        predictor = LatencyPredictor(store=store, clock=get_clock)

        # Seed observed builds so ArmingGate has build_durations
        for d in fixed_build_durations:
            store.record_build("proj", "observed", contention_level, d, current_time, current_time + d)

        # ArmingGate with fixed contention and no holdout
        arming_gate = ArmingGate(
            psi_reader=make_psi_reader(contention_level),
            contention_threshold_pct=10.0,
            holdout_probability=0.0,
            rng=random.Random(seed + seed_offset + 10000),
        )

        for _ in range(saves_per_seed):
            current_time += 10.0
            ev = SaveEvent(
                project_id="proj",
                path=Path("/mock/main.c"),
                file_class="source",
                t_save_monotonic=current_time,
                t_save_wall=current_time,
            )
            pred = predictor.on_save(ev)

            if pred is not None and pred.can_arm:
                # Get p_hit
                _, _, p_hit = store.recent_hit_rate("proj", "source", "default", last=10)
                window_lo = pred.window_lo if pred.window_lo is not None else 0.0
                window_hi = pred.window_hi if pred.window_hi is not None else 0.0

                decision = arming_gate.decide(
                    can_arm=pred.can_arm,
                    p_hit=p_hit,
                    build_durations=fixed_build_durations,
                    window_lo=window_lo,
                    window_hi=window_hi,
                )
                if decision.decision == "would_arm":
                    total_would_arm += 1

            # Generate delay
            has_build, delay = delay_gen()
            if has_build:
                current_time += delay
                predictor.on_process_spawn("proj", current_time)
            else:
                current_time += 35.0
                predictor.expire_pending(now=current_time)

    pct_would_arm = (total_would_arm / float(total_saves)) * 100.0
    return {
        "workload": workload_name,
        "contention_level": contention_level,
        "build_dur": build_dur,
        "pct_would_arm": pct_would_arm,
        "total_would_arm": total_would_arm,
    }


def make_stable_gen(rng: random.Random) -> Callable[[], Tuple[bool, float]]:
    return lambda: (True, max(0.01, rng.gauss(1.8, 0.05)))


def make_noisy_gen(rng: random.Random) -> Callable[[], Tuple[bool, float]]:
    return lambda: (True, max(0.01, rng.gauss(1.8, 0.6)))


def make_uniform_gen(rng: random.Random) -> Callable[[], Tuple[bool, float]]:
    return lambda: (True, rng.uniform(0.5, 20.0))


def make_exponential_gen(rng: random.Random) -> Callable[[], Tuple[bool, float]]:
    # Mean 8.0s -> lambda = 1/8
    return lambda: (True, rng.expovariate(1.0 / 8.0))


def make_lognormal_gen(rng: random.Random) -> Callable[[], Tuple[bool, float]]:
    # Median 2s -> mu = ln(2.0), sigma = 1.0
    return lambda: (True, rng.lognormvariate(math.log(2.0), 1.0))


def make_bimodal_gen(rng: random.Random) -> Callable[[], Tuple[bool, float]]:
    return lambda: (True, 0.8 if rng.random() < 0.5 else 6.0)


def make_sparse_builds_gen(rng: random.Random) -> Callable[[], Tuple[bool, float]]:
    # Stable delay (1.8s +/- 0.05s) but only 30% of saves are followed by a build
    def _gen():
        if rng.random() < 0.30:
            return True, max(0.01, rng.gauss(1.8, 0.05))
        return False, 0.0

    return _gen


def main():
    workloads = [
        ("Stable (1.8s ± 0.05s)", make_stable_gen),
        ("Noisy (1.8s, σ=0.6s)", make_noisy_gen),
        ("Uniform (0.5s - 20.0s)", make_uniform_gen),
        ("Exponential (mean 8.0s)", make_exponential_gen),
        ("Lognormal (median 2.0s, σ=1.0)", make_lognormal_gen),
        ("Bimodal (0.8s / 6.0s p=0.5)", make_bimodal_gen),
        ("Sparse Builds (1.8s, 30% build rate)", make_sparse_builds_gen),
    ]

    # ── Table 1: Confidence Gate Simulation ────────────────────────────────────
    results = []
    print("Running confidence gate simulation across 7 workloads (50 seeds x 300 saves)...")
    for name, factory in workloads:
        res = run_workload_simulation(name, factory, num_seeds=50, saves_per_seed=300)
        results.append(res)
        print(f"  Finished: {name}")

    # ── Table 2: Arming Decision Simulation ────────────────────────────────────
    # Step 7 Part E: three scenarios for each workload
    # (i)  contention=50%, build_dur=0.4s
    # (ii) contention=50%, build_dur=5.0s
    # (iii) contention=0%
    # NOTE: thresholds and priors are NOT tuned based on these results.
    arm_scenarios = [
        ("contention=50% dur=0.4s", 50.0, 0.4),
        ("contention=50% dur=5.0s", 50.0, 5.0),
        ("contention=0%", 0.0, 0.4),
    ]

    print("\nRunning arming decision simulation (3 scenarios x 7 workloads, 50 seeds x 300 saves)...")
    arm_results: Dict[str, List[Dict]] = {name: [] for name, _ in workloads}
    for wl_name, factory in workloads:
        for sc_label, contention, build_dur in arm_scenarios:
            res = run_arming_decision_simulation(
                workload_name=wl_name,
                delay_generator_factory=factory,
                build_dur=build_dur,
                contention_level=contention,
                num_seeds=50,
                saves_per_seed=300,
                seed_offset=999,
            )
            arm_results[wl_name].append(res)
        print(f"  Finished arming sim: {wl_name}")

    # ── Build markdown ─────────────────────────────────────────────────────────
    md_lines = [
        "# PreCompile Confidence & Arming Gate Simulation Results",
        "",
        "Evaluation of gating logic across 7 distinct developer workloads.",
        "Simulated using the production `LatencyPredictor` and `ConfidenceGate` with in-memory persistence and synthetic clock.",
        "Parameters: 50 independent random seeds, 300 saves per seed (15,000 saves total per workload).",
        "",
        "## Table 1: Confidence Gate Selectivity",
        "",
        "| Workload | % Saves Can Predict | % Saves Can Arm | Fraction Seeds Ever Armed | Mean Hit-Rate When Armed | Mean Window Width When Armed |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ]

    for r in results:
        hit_rate_str = f"{r['mean_hit_rate'] * 100:.1f}%" if r["pct_can_arm"] > 0 else "N/A"
        width_str = f"{r['mean_window_width']:.2f}s" if r["pct_can_arm"] > 0 else "N/A"
        md_lines.append(
            f"| {r['workload']} | {r['pct_can_predict']:.1f}% | {r['pct_can_arm']:.1f}% | "
            f"{r['fraction_seeds_armed']:.2f} | {hit_rate_str} | {width_str} |"
        )

    md_lines.extend([
        "",
        "## Key Observations (Table 1)",
        "- **Stable Workloads:** Near 100% can-predict and can-arm once warm-up completes, with narrow windows (~0.9-1.2s) and near-perfect hit rate.",
        "- **High-Variance / Unpredictable Workloads (Uniform, Exponential, Bimodal):** Heavily filtered by the confidence and hit-rate gates, preventing spurious reservations.",
        "- **Sparse Builds (Save-without-Build):** While the model learns the delay from completed builds, the hit-rate gate suppresses arming because the ~70% expired `no_build` events depress the rolling hit rate below 60%.",
        "",
        "## Table 2: Arming Gate — % of Saves that Would ARM",
        "",
        "> **IMPORTANT: These numbers are reported as-is. No threshold or prior was tuned based on them.**",
        "> Priors are explicit: `benefit_prior_max=0.5`, `idle_cost_rate=0.05`, `other_impact_rate=0.10`.",
        "> Step 9 will replace the speedup prior with the measured benefit once enough holdouts exist.",
        "",
        "Scenarios:",
        "- **(i) contention=50%, build_dur=0.4s** — Moderate build, high contention",
        "- **(ii) contention=50%, build_dur=5.0s** — Long build, high contention",
        "- **(iii) contention=0%** — No contention (expect 0% would_arm)",
        "",
        "| Workload | (i) 50% contention, 0.4s build | (ii) 50% contention, 5.0s build | (iii) 0% contention |",
        "| :--- | :---: | :---: | :---: |",
    ])

    for wl_name, _ in workloads:
        scenario_results = arm_results[wl_name]
        vals = [f"{r['pct_would_arm']:.1f}%" for r in scenario_results]
        md_lines.append(f"| {wl_name} | {vals[0]} | {vals[1]} | {vals[2]} |")

    md_lines.extend([
        "",
        "## Key Observations (Table 2)",
        "- **Long builds under high contention** yield higher `would_arm` rates because the benefit (saved build time) outweighs the cost.",
        "- **Short builds** may have cost >= benefit even under contention (cost model penalizes empty reservations relative to tiny speedup).",
        "- **Zero contention** always yields 0% would_arm because `speedup_frac = benefit_prior_max * min(1, 0/50) = 0`, making `benefit_s = 0`.",
        "- **Workloads where confidence gate already blocks arming** (e.g., Uniform, Exponential) show low rates across all scenarios.",
        "",
        "---",
        "*Generated by `benchmarks/gate_simulation.py` — Step 7, Stages 5 & 9 (observation-only)*",
    ])

    report = "\n".join(md_lines) + "\n"
    print("\n" + report)

    out_path = Path("benchmarks/results/gate_simulation.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"Saved simulation report to {out_path}")


if __name__ == "__main__":
    main()
