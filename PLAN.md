# PreCompile Implementation Plan

This roadmap outlines the 11 development steps to implement, test, and experimentally validate the anticipatory build scheduling system described in *IDF-B_PreCompile_Final.pdf*.

---

## Step 1: Recon & Repository Skeleton
- **Deliverable:**
  - Complete environment inspection across Linux subsystems (cgroup v2, CPU PSI, inotify, toolchains).
  - Specification summary (`SPEC_SUMMARY.md`) with components, equations, state machine, and validation protocols.
  - Python 3 skeleton package (`precompile/`) with all 11 modules mapped to Stages 1–10, typed signatures, and `tests/test_skeleton.py`.
  - Core design decisions documented in `README.md`.
- **How to Verify:**
  - Run `pytest tests/test_skeleton.py` to confirm all modules are cleanly importable and stubbed functions raise `NotImplementedError`.

---

## Step 2: File Event Monitor (Stage 1) - [COMPLETED]
- **Deliverable:**
  - Implementation of `precompile/monitor.py` using Linux `inotify` via `ctypes` on `libc` (`IN_CLOSE_WRITE`, `IN_MOVED_TO`).
  - Burst debouncing coalescing multiple saves within configurable window ($0.15\text{ s}$ default) to a single `SaveEvent` stamped with the timestamp of the last write.
  - Path filters excluding VCS directories (`.git`, `.hg`, `.svn`), build folders (`build`, `target`, `dist`, `out`, `node_modules`, `__pycache__`, `.cache`), editor swap/temp files (`*.swp`, `*.swx`, `*~`, `.#*`, `#*#`, numeric vim probe files such as `4913`, `*.tmp`), and build outputs (`*.o`, `*.d`, `*.a`, `*.so`, `*.pyc`).
  - Build suppression toggle `suppress_project(project_id, on)` dropping saves during claimed builds.
  - Threading model using a background thread running `select` with inotify fd, wakeup pipe, and debounce timers; supporting event queues and callbacks.
  - Standalone manual demo CLI `python3 -m precompile.monitor <path>`.
- **How to Verify:**
  - Unit tests in `tests/test_monitor.py` under `/tmp` (native ext4/tmpfs) passing all requirements (burst debouncing, spaced saves, ignored directories, editor temp files, atomic save rename pattern, dynamic subdirectory creation, project suppression, file classification, and zero thread/fd leakage).

---

## Step 3: Process Watcher & Matcher (Stages 2 & 7) - [COMPLETED]
- **Deliverable:**
  - Implementation of `precompile/watcher.py` with `/proc` polling (configurable 50 ms normal mode, 10 ms intensified mode), PID discovery, robust `/proc/<pid>/stat` comm parsing splitting on last ')', `t_start_monotonic` calculated from `starttime_ticks / CLK_TCK` against `CLOCK_BOOTTIME` and `CLOCK_MONOTONIC`, process table snapshots, parent mapping (`pid -> ppid`), callback/thread-safe queue delivery, and overhead statistics tracking.
  - Implementation of `precompile/matcher.py` with `ToolProfile` dataclass, regex matching for versioned and cross-compilation target prefixes, canonical path containment check distinguishing `/proj` from `/proj-other` and resolving symlinks, pluggable `/proc/<pid>/cgroup` reader checking PreCompile cgroup membership, `resolve_topmost_ancestor` selecting root candidate or earliest `t_start`, `get_descendants` traversing complete subtrees, and the `IdentityMatcher` class.
- **How to Verify:**
  - Unit tests in `tests/test_watcher.py` and `tests/test_matcher.py` passing all 11 core requirements (a through k) plus thread safety, overhead monitoring, and fallback tests under `/tmp`.

---

## Step 4: SQLite Data Store (Stages 3, 4, 8, 9) - [COMPLETED]
- **Deliverable:**
  - Implementation of `precompile/store.py` (`DataStore` / `Store`) using Python `sqlite3`, WAL mode, foreign keys (`PRAGMA foreign_keys = ON`), busy timeout (5000 ms), and `NORMAL` synchronous mode for file DBs.
  - Full schema implementation: `projects`, `delay_samples`, `model_state` (persisted EWMA state across restarts), `predictions` (supporting shadow and armed predictions, with 'no_build' counted as miss), and `builds` (contention-tagged historical durations).
  - Idempotent schema migration via `schema_version` table.
  - Concurrency model: single `check_same_thread=False` connection protected by `threading.RLock()`, enabling thread-safe access from watcher, monitor, and daemon threads.
  - Pruning mechanism with window functions (`ROW_NUMBER() OVER`) keeping newest N rows per key, auto-triggered on writes.
  - CSV export utility for downstream Stage 11 experimental evaluation.
  - Configuration in `StoreConfig` with non-root fallback to `~/.local/share/` and `/mnt/` mount warnings.
- **How to Verify:**
  - Unit tests in `tests/test_store.py` passing all 12 requirements (a through l) covering project CRUD and cascade deletion, 50-sample ordering, model state persistence across reopen, rolling hit-rate calculation, shadow prediction rules, build conditions, WAL/FK pragmas, 4-thread concurrent inserts (400 rows), retention pruning, and CSV export.

---

## Step 5: Predictor, Confidence Gate & Window Calculation (Stages 3 & 4) - [COMPLETED]
- **Deliverable:**
  - Implementation of `precompile/config.py` with `PredictorConfig` dataclass and backward-compatible property aliases.
  - Implementation of `precompile/gates.py` with `ConfidenceGate` and `GateResult`, supporting both strict observation-only warm-up (`strict_min_predictions_to_arm=True`) and loose arming evaluation.
  - Implementation of `precompile/predictor.py` featuring:
    - `LatencyModel` maintaining EWMA $\mu$ and $\sigma^2$ ($\alpha = 0.2$), strictly updating variance with the old mean prior to updating the mean.
    - Outlier clipping with dispersion floor ($s = \max(\sigma, \text{clip\_sigma\_floor\_s})$) when $n \ge 5$, 30s correlation window rejection, and negative delay tolerance/clamping.
    - Prediction window calculations: $[ \max(0, \mu - 2\sigma - 0.3), \mu + 2\sigma + 0.3 ]$ relative and absolute.
    - `LatencyPredictor` orchestrator backed by SQLite `DataStore`, lazy loading, thread-safe synchronization (`RLock`), injectable clock, pending save replacement tracking, first-matching spawn scoring, expired save scoring as `no_build`, and explainable `describe()` diagnostics.
  - Design decisions documented in `README.md` (clipping floor rationale and strict vs. loose observation warm-up).
- **How to Verify:**
  - Comprehensive unit test suites in `tests/test_gates.py` and `tests/test_predictor.py` covering requirements `a` through `p`.
  - Verification run via `python3 -m unittest discover -s tests -v` passing all 56 tests cleanly in WSL.
  - Adaptation experiment executed: adapting from 1.8s to 3.0s delay takes 7 updates without clipping and 9 updates with clipping enabled (both within the spec's 5–10 build range).

---

## Step 6: Daemon & CLI in Observation Mode (Stages 1–6 Observation Pass) - [COMPLETED]
- **Deliverable:**
  - Complete observation-only daemon orchestrator in `precompile/daemon.py` wiring `DataStore`, `FileMonitor`, `ProcessWatcher`, `IdentityMatcher`, and `LatencyPredictor`.
  - Ordering robustness mechanism using a 2.0s sliding spawn buffer matching rapid builds occurring within debounce intervals.
  - Multi-project topmost ancestor resolution and earliest process selection per batch.
  - Periodic 50ms housekeeping thread handling pending save correlation window expirations, prediction window activity tracking for intensified watcher polling, build completion tracking, CPU PSI contention logging, and save suppression with a 0.3s tail.
  - Modular CLI entry point in `precompile/cli.py` and `precompile/__main__.py` with subcommands: `projects` (add/list/remove/enable/disable), `daemon` (with PID file and signal handling), `status` (human-readable and JSON format), and `predict` (detailed model and gating diagnostics). Strict rejection of `/mnt/` paths for projects and databases.
  - Developer workload simulation tool in `benchmarks/build_on_save.py` with atomic temp-rename save patterns and modes: `auto`, `manual-irregular`, and `save-only`.
  - Gate simulation benchmark script `benchmarks/gate_simulation.py` evaluating 7 developer workloads (50 seeds x 300 saves) with report saved to `benchmarks/results/gate_simulation.md`.
  - C sample project with Makefile in `benchmarks/sample_project/`.
- **How to Verify:**
  - Integration and unit tests in `tests/test_cli.py` and `tests/test_daemon.py` passing all requirements (a through l).
  - Multi-seed statistical test in `tests/test_predictor.py` (test j) replacing seed-tuning with 50-seed validation (`< 5.0%` armed on exponential workload).
  - Full test suite execution: `python3 -m unittest discover -s tests -v` passing all 68 tests across all modules.
  - Real end-to-end verification run on native ext4 filesystem (`~/precompile-demo`) running 14 build-on-save cycles with gcc/make, successfully learning the delay, scoring shadow predictions, and arming the gate.

---

## Step 7: Arming Gate (PSI Contention, Cost-Benefit, Holdout) (Stages 5 & 9) - [COMPLETED]
- **Deliverable:**
  - Extended PSI reader in `precompile/gates.py` parsing `/proc/pressure/cpu` (`some_avg10`, `some_avg60`, `some_avg300`, `some_total_us`, and `full` lines).
  - Implemented `PSISampler` keeping a rolling history of `(monotonic_time, some_total_us)` computing `recent_some_pct` over the last $\ge 1\text{s}$ with injectable clock and reader.
  - Implemented contention gate evaluating $\text{contention\_level} = \max(\text{some avg10}, \text{recent\_some\_pct})$ against configurable threshold (default $10.0\%$), and rising predicted contention ($\text{some avg10} \in [0.5 \times \text{threshold}, \text{threshold})$ and $\text{avg10} > \text{avg60}$).
  - Implemented cost/benefit model evaluating expected saved build time versus false reservation and system overhead costs using explicit priors (`benefit_prior_max = 0.5`, `idle_cost_rate = 0.05`, `other_impact_rate = 0.10`).
  - Implemented randomized holdout selector withholding qualifying saves at configurable rate (default $10\%$, `holdout_probability = 0.1`) with injectable/seedable RNG.
  - Implemented `ArmingGate.decide()` executing the pipeline in order: confidence gate $\to$ contention gate $\to$ cost gate $\to$ holdout selector, with individual gate bypass toggles.
  - Schema migration v2 in `precompile/store.py` adding `decision`, `contention_level`, `benefit_s`, and `cost_s` to `predictions` via idempotent `ALTER TABLE`, plus `set_prediction_decision`, `set_prediction_holdout`, `recent_build_durations`, and `decision_counts`.
  - Daemon wiring evaluating `ArmingGate.decide()` on every save that produces a prediction, logging formatted `[arm?]` lines, recording decision metadata without arming reservations (`armed = False`), and preserving zero cgroup manipulation.
  - CLI enhancements: absolute and user-expanded path resolution in `projects add`, live SIGHUP reload notification, and decision count presentation in `status` and `predict --json`.
- **How to Verify:**
  - Unit and integration tests in `tests/test_gates.py` verifying requirements a through k (PSI parsing, contention thresholds and rising rule, cost/benefit formula validation, no-build-estimate abstention, gate bypass toggles, holdout binomial band check, schema migration v2, mocked daemon integration, SIGHUP reload, absolute path resolution, and CLI decision counts).
  - Extended benchmark `benchmarks/gate_simulation.py` with Table 2 across 7 workloads and 3 contention scenarios (50 seeds $\times$ 300 saves), saved to `benchmarks/results/gate_simulation.md`.
  - Real end-to-end contention demo in `benchmarks/run_demo_contention.sh` on native ext4 filesystem under `stress-ng` CPU saturation, confirming real-time PSI elevation and transition from `abstain: no contention` to `would ARM` / `HOLDOUT`.

---

## Step 8: Reservation Manager & Cgroup v2 Controller (Stages 6, 7, 8)
- **Step 8a: Cgroup Controller, Reservation Manager & Proof Test - [COMPLETED]**
  - Implementation of `precompile/cgroup.py`:
    - Hierarchical sibling placement rule: reservations placed as direct children of cgroup v2 root (`/sys/fs/cgroup/precompile-res-<id>`) competing directly with sibling slices (`init.scope`, `user.slice`) at default `cpu.weight=100`.
    - Safety controls: weight clamped to `[1, weight_cap]` (default 500); scheduling classes and priorities never modified; failures raise `CgroupError`.
    - Complete interface: `ensure_cpu_controller`, `create_group`, `set_weight`, `move_pid`/`move_pids`, `list_procs`, `is_populated`, `original_cgroup_of`, `remove_group`, `release_group`, `list_stale_groups`, `cleanup_stale`.
    - In-memory `FakeCgroupBackend` for testing without root.
  - Implementation of `precompile/reservation.py`:
    - Full lifecycle state machine: `IDLE`, `ARMED`, `ACTIVE`, `CLAIMED`, `EXPIRED`, `CLOSED`, `CANCELLED` (including early match `ARMED -> CLAIMED`).
    - At most 1 reservation per project with newer-save window resets; global capacity ceiling (`max_reservations=4`).
    - Atomic test-and-set claim with process and descendant migration; non-atomic check-then-set mode with hook for concurrency ablation.
    - Max lifetime enforcement (`clamp(5 * median_build, 5s, 120s)`) reverting weight to 100 on timeout.
    - Graceful degradation: non-root environments fall back safely with logged warning.
  - Schema migration v3 in `precompile/store.py` adding `reservations` table and helper methods `record_reservation`, `list_reservations`, and `reservation_summary`.
  - CLI subcommands: `precompile reservations [--project X]` and `precompile cleanup`.
  - The Proof Test (`tests/test_cgroup_effect.py`): measured ratio of 4.87:1 (boosted vs normal on 1 core) and 27.6:1 against 8 competitors.
- **Step 8b: Daemon Actuation & End-to-End Real Boost Verification - [COMPLETED]**
  - **Part A Fixes:**
    - Watcher exec detection (`precompile/watcher.py`): tracks `_first_seen` timestamps and re-inspects exe symlinks for PIDs $\le 0.5$s old, detecting in-place execs (e.g. `bash sleep -> exec fake-gcc` and `taskset -c 0 <tool>`).
    - Accurate build timing (`precompile/exit_monitor.py`): background `ProcessExitMonitor` using `os.pidfd_open` and `select.poll` with an internal wake pipe, measuring build runtimes within $\approx 20$ms of real execution.
    - Claim/tick race prevention (`precompile/reservation.py`): added `move_done: bool` flag preventing premature empty-group cleanup until process migration is complete; prunes completed reservations keeping the last 50.
    - PID reuse guard (`precompile/reservation.py`): re-checks `/proc/<pid>/stat` starttime ticks before cgroup migration.
  - **Part B Daemon Actuation:**
    - Added `--actuate`, `--holdout-prob`, and `--weight` CLI flags to `precompile daemon`.
    - Wired `ReservationManager` into `PreCompileDaemon`: arms reservations on `would_arm`, claims matching spawns, persists state transitions, records build conditions (`reserved`, `holdout`, `observed`), and cleans stale cgroups on startup and shutdown.
  - **Part C Heavy Workload Calibration:**
    - Created `benchmarks/gen_heavy.py` generating `big.c` with 240 functions (~1.9s clean build time).
    - Verified CPU pressure slowdown under pinned load (0-1): idle 1.90s (1.00x) $\rightarrow$ high 9.08s (4.79x slowdown, PSI avg10 = 94.4%).
  - **Part D Root Actuation Tests:**
    - Full root actuation test suite in `tests/test_root_actuation.py` testing fallback, arming, claiming, cgroup residency, expiration, descendant migration, SIGTERM cleanup, and stale group deletion. All 6 root tests passed.
  - **Part E A/B Smoke Benchmark (`benchmarks/ab_smoke.sh`) & Step 8c Descendant Fix:**
    - **Step 8c Diagnostic (H1 Confirmed):** Initial A/B run in 8b showed bimodal reserved build times (mean 6.45s vs holdout 8.38s, but medians 8.04s vs 8.33s). Code audit and runtime leak probes confirmed H1: `try_claim()` in `reservation.py` re-used a stale watcher snapshot to re-scan descendants. As a result, late-forked compiler subprocesses (e.g. `cc1`) were never moved into the cgroup and ran in `/init.scope` with unboosted priority.
    - **Fixpoint Descendant Sweeper:** Implemented `_get_all_descendants_proc()` and `_sweep_cgroup_fixpoint()` in `precompile/reservation.py` polling `/proc` in a fixpoint loop every 5ms over `sweep_window_s = 0.3s`. All compilation descendants (including `cc1`) are reliably moved into the reservation group before `move_done` is asserted.
    - **Live Probing & Instrumentation:** Added zero-leak logging `[leak]` at +0.3s and +1.0s, and automated `[cgroup_cpu]` usage readings upon cgroup release. Added child `cpu_share` measurements via `RUSAGE_CHILDREN` in `build_on_save.py`.
    - **Post-Fix Full A/B Run (12 idle warm-up + 60 contention cycles):**
      - 33 `reserved` vs 27 `holdout` builds.
      - Reserved median: **3.237s** (mean: 3.419s, p95: 4.253s, CPU share: 90.5%).
      - Holdout median: **14.495s** (mean: 14.633s, p95: 16.134s, CPU share: 21.8%).
      - Speedup: **77.7% faster** (relative speedup factor of 4.48x under heavy contention).
      - Mann-Whitney U test: $U = 0.0$, $z = -6.6196$, $p = 3.60 \times 10^{-11}$ (strictly separated distributions, non-bimodal).
      - Claim efficiency: 100% claim rate (33/33), mean latency 27.18 ms, median 27.00 ms.
      - Zero leftover `precompile-res-*` cgroups in `/sys/fs/cgroup`.


---

## Step 9: Self-Measured Benefit Estimator (Stage 9) - [COMPLETED]
- **Deliverable:**
  - **Part A Close-outs from Step 8c:**
    - Non-blocking fixpoint descendant sweeper: launched in dedicated daemon thread `sweep-{res.id}` with injectable `proc_enumerator` and `sweep_window_s` configuration. `try_claim()` returns immediately in $< 0.1$ms. Maintained `move_done = False` during sweep window to prevent premature closure.
    - Sweeper-OFF ablation benchmark (`benchmarks/ab_smoke.sh --no-sweeper`): empirically confirmed that without sweeping, descendants leak on 57.1% of runs (4 of 7), collapsing CPU share to $0.0\%-0.6\%$ and slowing builds to $8.37\text{s}-9.70\text{s}$ (matching unboosted holdouts). With sweeper enabled, 100% of descendants are captured with zero leaks and CPU share is $62.0\%-91.4\%$.
    - Rapid-fork live cgroup stress test (`test_j_fixpoint_sweep_rapid_fork_no_leaks`): ran 50 rapid fork-and-exit cycles; 0 leaks and 0 leftover cgroups.
  - **Part B Schema Migration v4 (`precompile/store.py`):**
    - Added `benefit_state` and `benefit_evals` tables.
    - Added store accessors: `get_benefit_state()`, `set_benefit_state()`, `record_benefit_eval()`, `list_benefit_evals()`.
    - Extended `export_csv` for both new tables; verified migration idempotency and backward compatibility.
  - **Part C Statistical Testing & Benefit Estimator (`precompile/stats.py`, `precompile/benefit.py`):**
    - Pure-Python statistical testing in `precompile/stats.py`: Mann-Whitney U test with exact small-sample evaluation ($n_1 + n_2 \le 20$ via DP and tie permutations) and normal approximation with tie correction for larger samples; stratified bootstrap confidence intervals (1,000 iterations); Stouffer's weighted Z-score combination.
    - Closed-loop priority feedback controller (`BenefitEstimator`): matches `reserved` and `holdout` builds stratified across contention bins (`[0,10)`, `[10,30)`, `[30,60)`, `[60,100]`). Sample size guards require at least `min_total_n` per arm and `min_bin_n` in at least one bin before acting.
    - Dynamic weight decay sequence: $500 \to 250 \to 125 \to 100 \to \text{disabled}$ upon non-significant or negative speedup.
    - Exploratory probe reservations ($p = 0.20$) when arming is disabled, stepping priority back up when significant benefit is detected under contention.
  - **Part D Daemon & CLI Integration:**
    - Wired `BenefitEstimator` into `PreCompileDaemon`: queries dynamic project weight, checks probe eligibility, passes measured speedup to `ArmingGate.decide()`, and triggers evaluation cadence on build exit.
    - Added `--min-bin-n`, `--no-cost-gate`, and `--leak-check` CLI/daemon flags.
    - CLI commands: added `precompile benefit [--project X] [--json]` and integrated benefit metrics into `precompile status`.
  - **Part E Verification & Live Closed-Loop Benchmark:**
    - Full test suite: 167 tests passing (0 failures, 12 skipped without root, 0 skipped under root).
    - Closed-loop live benchmark (`benchmarks/benefit_demo.sh`): verified full cycle of idle decay ($500 \to 250 \to 125 \to 100 \to \text{disabled}$), followed by exploratory probes under high contention (detecting $70.9\%-73.6\%$ boost in high-contention bin, $p < 0.001$), stepping weight back up to 200 then 400 with arming re-enabled.
    - Bounded-harm idle overhead benchmark (`benchmarks/idle_overhead.sh`): 40 cycles at idle showed zero statistically significant overhead between reserved (median 1.836s) and holdout (median 1.816s), with 95% bootstrap CI $[-2.56\%, +2.09\%]$ and $p = 0.5606$.
    - Verified zero leftover cgroups in `/sys/fs/cgroup`.

---

## Step 9b: Close-Out Items & Robustness - [COMPLETED]
- **Deliverable:**
  - Exhaustive test edit and assertion change report across all test modules.
  - Injected `starttime_reader` in `ReservationManager` decoupling unit tests from host PID table; restored clean fake PIDs.
  - Exact small-sample Mann-Whitney U test with and without ties verified against hand-computed values.
  - Sample size guards (`min_total_n`, `min_bin_n`) preventing premature decay on small samples (e.g. 4 vs 7 builds).
  - Probe actuation fix in `daemon.py` ensuring exploratory probes arm correctly.
  - Measured idle actuation overhead (relative effect $-1.10\%$, 95% CI $[-2.56\%, +2.09\%]$, $p=0.5606$).
  - Breakdown of build durations by applied reservation weight.

---

## Step 10: Safety Rules, Systemd Service & Ablation Flags (Stage 10) - [COMPLETED]
- **Deliverables:**
  - **Part A Estimator Consistency & Close-outs (`precompile/benefit.py`, `precompile/stats.py`):**
    - Effect, bootstrap CI, and p-value computed from identical eligible bins ($n_R \ge \text{min\_bin\_n}$, $n_H \ge \text{min\_bin\_n}$).
    - Per-bin estimates ($n_R, n_H$, effect, 95% CI, p-value) reported as primary output in logs, database (`per_bin_json`), and CLI; pooled numbers explicitly labelled as `[pooled]`.
    - Decision set gating: when contention gate is enabled (default), only bins with contention $\ge \text{contention\_threshold\_pct}$ (10.0%) count towards the decision. If no decision set bins are eligible, controller takes action `'none'`.
    - Added unit tests: heterogeneous idle/contended data, reproduction of evals 5 & 6, single-arm eligible bin exclusion.
  - **Part B Safety, Bounded Harm & Fallback (`precompile/safety.py`):**
    - AST test scanner enforcing zero calls to real-time scheduling APIs (`sched_setscheduler`, `SCHED_FIFO`, `SCHED_RR`, `SCHED_DEADLINE`, `chrt`, `setpriority`, `os.nice`).
    - Runtime check with `os.sched_getscheduler` ensuring process is in `SCHED_OTHER` (0), `SCHED_BATCH` (3), or `SCHED_IDLE` (5) before touching cgroup.
    - Weight cap validation: system ceiling 1000, default 500, with clamping.
    - Rolling boost budget (900s / 3600s interval) persisted in `reservations` table and tracked across daemon restarts; refuses arming with `abstain_budget` when budget is exhausted.
    - Circuit breaker: 5 consecutive actuation failures trigger 300s observation-only cooldown.
    - Kill switch: `precompile pause` / `precompile resume` flag file mechanism.
    - Privilege degradation: structured reasons (`not_root`, `cgroup_v1`, `no_cpu_controller`, `parent_not_writable`) with graceful observation-only fallback.
  - **Part C Systemd Unit (`packaging/precompile.service`):**
    - Systemd service configuration (`Type=simple`, `Restart=on-failure`, `StateDirectory=precompile`, `RuntimeDirectory=precompile`, `ExecStopPost=/usr/bin/python3 -m precompile cleanup`).
    - Verified in WSL systemd: install, daemon-reload, start, 1 save-and-build, stop with 0 leftover cgroups, kill -9 crash restart + stale cgroup cleanup, uninstall.
  - **Part D Baseline Conditions & Ablation Switches (`--condition`, `--ablation-*`):**
    - Conditions: `c1-reactive` (reactive boost on matching process + contention $\ge 10.0\%$, normal watcher), `c2-full` (default anticipatory system), `c3-static` (always-on boost, always intensified watcher).
    - Ablation switches: `--ablation-no-confidence-gate` (A2), `--ablation-no-contention-gate` (A3), `--ablation-fixed-window LO,HI` (A4), `--ablation-no-holdout` (A5).
    - Migration v5: `daemon_runs` table tracking condition, flags JSON, code version, and start time.
    - Flag validation rejecting invalid combinations (C1 with A2/A4/A5; C3 with any ablation).
    - Root smoke test: 10 cycles on heavy project for c1, c2, c3 under high CPU load; 0 leftover cgroups.
- **Verification:**
  - 187 unit and integration tests passing cleanly under non-root and root (0 failures, 0 errors).
  - Systemd service lifecycle verified live under systemd in WSL.
  - Root smoke test comparison table generated across C1, C2, C3.

---

## Step 11: Experimental Evaluation Harness (Section 8)
- **Step 11a (Harness, Plan, Mechanism Tests & Pilot):** [COMPLETED]
  - Pre-registered experiment plan written to `benchmarks/EXPERIMENT_PLAN.md` with timestamp.
  - Plan-driven experiment runner implemented in `benchmarks/run_experiments.py` supporting `--tier`, `--dry-run`, `--pilot`, and `--resume`.
  - Statistical analysis suite implemented in `benchmarks/analyze_experiments.py` with pure-Python Mann-Whitney U test, step-down Holm-Bonferroni correction, and 95% bootstrap confidence intervals.
  - Mechanism tests in `benchmarks/mechanism_tests.py` verifying atomic claim concurrency guard (1 success out of 10 concurrent racers) and holdout distribution.
  - Harness unit tests in `tests/test_experiments_harness.py` (7 tests passing).
  - Pilot benchmark run executed across 8 blocks with 24 measured builds; verified 0 leftover cgroups and processes.
  - Step 10 close-outs completed (systemd actuation verification, status mode reporting, watcher scan rate transparency, steady-state smoke table, non-destructive kill-switch).

- **Step 11b (Full Experimental Evaluation):** [COMPLETED]
  - Full evaluation matrix executed across Tier 1 (Scenario A: idle, moderate, high load), Tier 2 (Scenario B irregular delays, Scenario C save-only), and Tier 3 (Ablations A2, A3, A4, A5) totaling 94 blocks, 900 measured builds, and 200 save iterations.
  - Final evaluation report and verification of all pre-registered success criteria (S1–S7) with step-down Holm-Bonferroni correction and 95% bootstrap confidence intervals.
  - Verification of 0 leftover `precompile-res-*` cgroups and 0 orphaned background processes across all tiers.

- **Step 11f (Concurrent Process Claiming Validation - Plan Amendment v1.3):**
  - **Hypothesis:** Under concurrent process creation by near-simultaneous candidate processes, atomic claim protection guarantees that exactly one process is claimed into the reservation cgroup (P1), processes from other projects or outside any project are never admitted (P2), all live descendants of the claimant are swept into the group without leaks (P3), and the reservation and cgroups clean up with zero orphans upon termination (P4). Under the negative control (atomic claim OFF), if the daemon architecture's single-threaded topmost-ancestor resolution naturally serializes candidates before the reservation lock is exercised, the negative control will observe 0 double claims (indicating the OS-level test does not exercise the race at the reservation lock level); if interleaving occurs, duplicate claims will be observed.
  - **Design:**
    - Shared-pipe barrier launcher: forks $N$ child processes that block reading from a pipe. On write-pipe close, all children unblock simultaneously and execute the target tool profile binary (`gcc`) with `cwd` inside the project.
    - Deterministic arming: reservation is armed with window open ($[0.0, 5.0]\text{ s}$) before the barrier is released.
    - 4 Variants:
      - **V1:** All $N$ candidates in target project.
      - **V2:** $N$ target candidates plus $N$ decoys in a second registered project that is also armed.
      - **V3:** $N$ target candidates plus $N$ decoys outside any registered project.
      - **V4:** $N$ target candidates, each candidate forks 4 children immediately after exec.
    - Candidate counts: $N \in \{2, 8, 32\}$.
    - Conditions: Atomic claim ON (default) and Atomic claim OFF (negative control via `--ablation-no-atomic-claim`).
    - Trial counts: At least 100 trials per $(V, N, \text{condition})$ tuple ($4 \times 3 \times 100 \times 2 = 2,400$ total trials).
  - **Success Criteria (per trial, atomic claim ON):**
    - **P1:** Exactly one candidate is the claimant (`cgroup.procs` of the group and DB claim record agree).
    - **P2:** No process of another project, or outside any project, is in the group.
    - **P3:** In V4, every live descendant of the claimant is in the group (`leaks == 0`).
    - **P4:** The reservation reaches CLOSED, the group directory is removed, no orphan cgroups remain.
    - Pass requirement: P1–P4 hold in 100% of trials. Clopper-Pearson 95% upper bound on failure rate reported.
  - **Negative Control Evaluation:**
    - Report the count and percentage of trials with more than one claimant under atomic claim OFF.
    - If OFF never produces a duplicate claim, explicitly report that the test does not exercise the race at the reservation lock level due to daemon-level serialization, and do not describe atomic claim as validated by this test.
  - **Secondary Metric:**
    - Creation-to-claim latency: duration from the first candidate creation timestamp to claim timestamp (median, p95).
  - **Analysis:**
    - Pure-Python analyzer reading only the raw JSONL trial logs.

- **Step 11f-2 (Concurrent Claiming Pre-Registered Corrected Re-Run & Lock-Level Race - Plan Amendment v1.4):**
  - **Objectives & Scope:**
    - Maintain unmodified success criteria P1–P4 and the strict 100% pass requirement.
    - Resolve the harness measurement artifact identified in Run 1 where high concurrency ($N=32$) delayed process claiming past a fixed 80ms sleep while candidates had an abbreviated 0.20s lifetime.
    - Provide direct validation of the lock-level concurrency race bypassing daemon watcher serialization.
  - **Measurement Fixes & Protocol Updates:**
    - **Event-Driven Live Snapshot:** Replace the fixed 0.08s sleep with an event-driven poll loop on the SQLite database and cgroup, capturing `group_members` immediately once the claim is registered.
    - **Lengthened Candidate Lifetime:** Extend candidate process execution duration to $\ge 0.80\text{ s}$ to ensure no candidate process exits prior to snapshot evaluation.
    - **Telemetry Schema Extension:** Record `cand_dur_s` (candidate duration), `t_snapshot` (monotonic snapshot timestamp), and the repository `plan_commit` (HEAD commit hash) in every trial record.
    - **Decoy Teardown Synchronization:** In multi-reservation variants (V2), explicitly synchronize the teardown of both target and decoy reservation groups before orphan evaluation.
    - **Trial-by-Trial Interleaving:** Interleave Atomic ON and Atomic OFF conditions trial-by-trial within each cell rather than executing condition blocks sequentially.
    - **Re-run Target Matrix:** Re-run cells V2/N32, V3/N32, and V4/N32 (100 trials each for ON and OFF, interleaved, totaling 600 trials). Cells that cleanly passed in Run 1 (V1/N2, V1/N8, V1/N32, V2/N2, V2/N8, V3/N2, V3/N8, V4/N2, V4/N8) remain as established in Run 1.
  - **Lock-Level Concurrency Race Test:**
    - **Design:** Directly test `ReservationManager.try_claim` under true multithreaded contention with real OS processes, bypassing `ProcessWatcher` serialization.
    - **Setup:** For $N \in \{2, 8, 32\}$, spawn $N$ real OS child processes holding open pipes. Spawn $N$ concurrent worker threads synchronized by a `threading.Barrier(N)`. At the barrier release, all $N$ threads simultaneously invoke `ReservationManager.try_claim` on an armed reservation backed by real Linux cgroups (`CgroupController`).
    - **Conditions:**
      1. `atomic_claim = True` (Default Atomic Claim ON).
      2. `atomic_claim = False` without interleave hook (Ablated Non-Atomic Claim).
      3. `atomic_claim = False` with `claim_interleave_hook` (Negative Control with Interleaving Hook).
    - **Volume:** At least 1,000 independent rounds per $N \in \{2, 8, 32\}$ for each condition.
    - **Criteria:**
      - **Atomic Claim ON:** Exactly one claimant thread succeeds (`try_claim == True`) in 100% of at least 1,000 rounds for each $N \in \{2, 8, 32\}$ (0 duplicate claims).
      - **Atomic Claim OFF with Hook:** Negative control must produce more than one winner in at least one round; otherwise the negative control is reported as failed.


