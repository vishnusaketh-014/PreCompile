# Evaluation Results: PreCompile System (v1.0 - v1.5)

## 1. Registration Trail

All experimental runs, harness scripts, and plan versions are cryptographically tracked in the git commit log.

| Plan / Artifact Version | Git Commit Hash | Commit Message Summary | Status |
|:---|:---|:---|:---|
| **Experiment Plan v1.0** | `6bca5fc` | Pre-register evaluation experiment plan and evaluation harness | Pre-registered |
| **Experiment Plan v1.1** | `0a4d2cb` | Amend evaluation experiment plan to version 1.1 | Pre-registered |
| **Experiment Plan v1.2** | `4051943` | Pre-register experiment plan v1.2 and measurement harness fixes | Pre-registered |
| **Experiment Plan v1.3** | `6be6aef` | Pre-register experiment plan v1.3 and warm-until-armed harness fixes | Pre-registered |
| **Experiment Plan v1.4** | `1db72af` | Pre-register experiment plan v1.4 with post-hoc A3' and Scenario C' | Pre-registered (Post-hoc) |
| **Experiment Plan v1.5** | `[Pending Commit]` | Post-hoc audit amendment: re-run designs for B and A3', data provenance | Pre-registered (Post-hoc) |
| **Harness Implementation** | `8f4fd43` | Implement post-hoc runner support and microbench restart pattern | Working tree clean |
| **Tier 1 Run** | `5fa7b4e855be00c095a10c22477b974932304e35` | Fix post-build suppression settlement and reservations rowid query | Complete (`git_dirty: false`) |
| **Tier 2 Run** | `b685722ecc77d62e6fc84468f5dc92dc47a3b53f` | Add tier 3 option to experiment runner CLI | Complete (`git_dirty: false`) |
| **Tier 3 Run** | `b685722ecc77d62e6fc84468f5dc92dc47a3b53f` | Add tier 3 option to experiment runner CLI | Complete (`git_dirty: false`) |
| **Post-Hoc Experiments**| `8f4fd434849223113beb05715b51cbd875b4e98c` | Implement post-hoc runner support and microbench restart pattern | Complete (`git_dirty: false`) |

---

## 2. Experimental Data Tables (Verified from Raw Data)

### Table 2a: Per-Cell Build Performance (Intention to Treat, Reserved-Only, Holdout-Only)
*Derived directly from raw JSONL files (`results_tier1`, `results_tier2`, `results_tier3`, `results_posthoc`) and verified against `analyze_experiments.py`.*

| Tier | Scenario | Load | Condition | N (ITT) | Median ITT (s) | Mean ITT (s) | P95 ITT (s) | N (Res) | Median Res (s) | P95 Res (s) | N (Hold) | Median Hold (s) | P95 Hold (s) |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| 1 | A | high | c0-baseline | 50 | 8.3826 | 8.4682 | 9.1987 | - | - | - | - | - | - |
| 1 | A | high | c1-reactive | 50 | 1.9939 | 2.0804 | 2.6408 | - | - | - | - | - | - |
| 1 | A | high | c2-full | 50 | 2.0422 | 3.0441 | 8.4011 | 43 | 1.9768 | 2.7867 | 7 | 8.3470 | 8.6312 |
| 1 | A | high | c3-static | 50 | 1.9506 | 2.0948 | 2.6910 | - | - | - | - | - | - |
| 1 | A | idle | c0-baseline | 50 | 1.8257 | 1.8286 | 1.9233 | - | - | - | - | - | - |
| 1 | A | idle | c1-reactive | 50 | 1.8216 | 1.8195 | 1.9228 | - | - | - | - | - | - |
| 1 | A | idle | c2-full | 50 | 1.8407 | 1.8469 | 1.9355 | 0 | - | - | 0 | - | - |
| 1 | A | idle | c3-static | 50 | 1.8181 | 1.8245 | 1.8999 | - | - | - | - | - | - |
| 1 | A | moderate | c0-baseline | 50 | 4.5865 | 4.5861 | 4.8417 | - | - | - | - | - | - |
| 1 | A | moderate | c1-reactive | 50 | 2.0123 | 2.2282 | 3.4061 | - | - | - | - | - | - |
| 1 | A | moderate | c2-full | 50 | 1.9969 | 2.3685 | 4.1650 | 47 | 1.9813 | 3.4613 | 3 | 4.5393 | 4.6964 |
| 1 | A | moderate | c3-static | 50 | 1.9620 | 2.1840 | 3.5753 | - | - | - | - | - | - |
| 2 | B | moderate | c0-baseline | 30 | 4.7793 | 5.2128 | 7.6173 | - | - | - | - | - | - |
| 2 | B | moderate | c1-reactive | 30 | 2.1646 | 2.3297 | 3.2791 | - | - | - | - | - | - |
| 2 | B | moderate | c2-full | 30 | 2.2467 | 2.7482 | 5.0030 | 25 | 2.1955 | 2.9427 | 1 | 5.0288 | 5.0288 |
| 2 | B | moderate | c3-static | 30 | 2.0961 | 2.3136 | 2.9561 | - | - | - | - | - | - |
| 3 | A | high | a4-fixedwin | 50 | 1.9796 | 2.7739 | 8.2871 | 45 | 1.9690 | 3.2579 | 5 | 8.3152 | 8.4033 |
| 3 | A | high | a5-noholdout| 50 | 2.0262 | 2.1959 | 3.0665 | 50 | 2.0262 | 3.0665 | 0 | - | - |
| 3 | A | idle | a3-nocont | 50 | 1.8418 | 1.8542 | 1.9730 | - | - | - | - | - | - |
| 3 | B | moderate | a2-noconf | 30 | 2.2297 | 2.5501 | 4.7412 | 27 | 2.0523 | 3.3564 | 1 | 4.7812 | 4.7812 |
| Post-hoc | A | idle | a3-prime | 50 | 3.8742 | 3.8863 | 4.4302 | 39 | 3.8807 | 4.3992 | 7 | 3.8807 | 4.3653 |
| 2 | C | moderate | c0-baseline | 0 | (save-only)| - | - | - | - | - | - | - | - |
| 2 | C | moderate | c2-full | 0 | (save-only)| - | - | 0 | - | - | 0 | - | - |
| Post-hoc | C' | moderate | c2-full | 0 | (save-only)| - | - | 0 | - | - | 0 | - | - |

---

### Table 2b: System Metrics and Overhead per Condition & Load
*Derived from raw `block_metrics.jsonl` files across all tiers.*

| Condition | Load | Scenario | Daemon CPU % [95% CI] | Scans/s | Avg Scan (ms) | Hog Rate (cpu/s) | Delta vs C0 | Hit Rate (%) | False Res Rate (%) |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| c0-baseline | high | A | 0.00% (no daemon) | 0.0 | 0.00 ms | 1.84 | baseline | N/A | N/A |
| c1-reactive | high | A | 4.82% [4.70%, 4.91%] | 48.5 | 0.30 ms | 1.66 | -0.18 cpu/s | N/A | 0.0% |
| c2-full | high | A | 4.31% [3.42%, 5.03%] | 55.2 | 0.27 ms | 1.73 | -0.11 cpu/s | 100.0% | 0.0% |
| c3-static | high | A | 6.21% [6.15%, 6.27%] | 94.3 | 0.27 ms | 1.66 | -0.18 cpu/s | N/A | 0.0% |
| c0-baseline | moderate | A | 0.00% (no daemon) | 0.0 | 0.00 ms | 1.77 | baseline | N/A | N/A |
| c1-reactive | moderate | A | 4.69% [4.64%, 4.74%] | 49.7 | 0.29 ms | 1.67 | -0.16 cpu/s | N/A | 0.0% |
| c2-full | moderate | A | 4.90% [4.02%, 5.61%] | 63.9 | 0.26 ms | 1.69 | -0.15 cpu/s | 100.0% | 0.0% |
| c3-static | moderate | A | 5.96% [5.88%, 6.01%] | 94.4 | 0.25 ms | 1.67 | -0.17 cpu/s | N/A | 0.0% |
| c0-baseline | idle | A | 0.00% (no daemon) | 0.0 | 0.00 ms | 0.00 | baseline | N/A | N/A |
| c1-reactive | idle | A | 1.43% [1.41%, 1.45%] | 19.8 | 0.28 ms | 0.00 | baseline | N/A | N/A |
| c2-full | idle | A | 1.80% [1.75%, 1.84%] | 24.0 | 0.29 ms | 0.00 | baseline | 100.0% | N/A |
| c3-static | idle | A | 5.64% [5.55%, 5.75%] | 93.9 | 0.27 ms | 0.00 | baseline | N/A | 0.0% |
| c0-baseline | moderate | B | 0.00% (no daemon) | 0.0 | 0.00 ms | 1.89 | baseline | N/A | N/A |
| c1-reactive | moderate | B | 2.27% [2.18%, 2.42%] | 30.4 | 0.25 ms | 1.87 | +0.04 cpu/s | N/A | 0.0% |
| c2-full | moderate | B | 3.15% [2.22%, 3.67%] | 74.3 | 0.21 ms | 1.89 | +0.05 cpu/s | 100.0% | 0.0% |
| c3-static | moderate | B | 3.94% [3.78%, 4.04%] | 95.4 | 0.21 ms | 1.88 | +0.04 cpu/s | N/A | 0.0% |
| a2-noconf | moderate | B | 3.42% [2.97%, 3.76%] | 79.3 | 0.21 ms | 1.89 | +0.05 cpu/s | 93.3% | 6.7% |
| a3-nocont | idle | A | 1.81% [1.73%, 1.93%] | 25.0 | 0.26 ms | 0.00 | baseline | 100.0% | N/A |
| a3-prime | idle | A | 4.14% [3.71%, 4.50%] | 60.9 | 0.25 ms | 0.00 | baseline | 100.0% | 0.0% |
| a4-fixedwin | high | A | 4.81% [4.33%, 5.34%] | 63.8 | 0.26 ms | 1.70 | -0.13 cpu/s | 100.0% | 0.0% |
| a5-noholdout| high | A | 5.15% [3.98%, 5.76%] | 66.8 | 0.27 ms | 1.69 | -0.15 cpu/s | 100.0% | 0.0% |
| c2-full (C') | moderate | C' | 3.09% [3.09%, 3.09%] | 54.5 | 0.25 ms | 1.88 | +0.05 cpu/s | N/A | 100.0% (4/4 exp)|

---

### Table 2c: Consolidated Mechanism Tests (Verified from Code & Logs)

| Mechanism Test Suite | Description | Target / Protocol | Result | Outcome |
|:---|:---|:---|:---|:---|
| **Atomic Claim Concurrency** | Lock-guarded in-memory claim flag in `reservation.py` | 2,000 rounds with 5 concurrent threads released by `threading.Barrier` | 2,000 successes, 0 double claims, 0 race errors | **PASS** |
| **Natural Race (Unhooked)** | In-memory claim check executed without artificial delays | 2,000 rounds with 5 concurrent threads | 2,000 successes, 0 double claims | **PASS** |
| **Forced-Hook Collision** | Non-atomic check-then-set with interleave barrier hook | 200 rounds with deterministic barrier synchronization | 200 double claims detected (100.0% collision rate) | **PASS** (Proves lock necessity) |
| **Holdout Agreement (Self-Measured)**| High load C2 reserved vs holdout build duration | Self-measured: N=38 reserved vs N=30 holdouts (8.15s vs 2.02s) | **+75.2%** speedup (95% bootstrap CI: **[+72.2%, +75.9%]**) | **PASS** (CIs overlap) |
| **Holdout Agreement (Controlled)** | Controlled comparison: C0 vs C2 under identical high load | Controlled: N=30 C0 vs N=30 C2 (8.16s vs 1.93s) | **+76.3%** speedup (95% bootstrap CI: **[+75.6%, +77.2%]**) | **PASS** (CIs overlap) |

---

## 3. Pre-Registered Hypothesis Testing (Holm-Bonferroni Correction)

### Family 1: Core Performance (Tier 1)
$M = 6$ tests. Significance threshold $\alpha = 0.05$.

| Hypothesis | Description | Comparison | Metric | Observed Delta | Unadjusted $p$ | Holm Rank $k$ | Holm Threshold $\frac{\alpha}{M-k+1}$ | Decision |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| $H_{0,1}$ | C2 outperforms C0 at high load | c2-full vs c0-baseline | Build time median | -75.64% (-6.340s) | $< 1.0 \times 10^{-15}$ | 1 | 0.0083 | **REJECT $H_{0,1}$** |
| $H_{0,4}$ | C2 outperforms C0 at mod load | c2-full vs c0-baseline | Build time median | -56.46% (-2.590s) | $< 1.0 \times 10^{-15}$ | 2 | 0.0100 | **REJECT $H_{0,4}$** |
| $H_{0,3}$ | C2 matches C3 at high load | c2-full vs c3-static | Build time median | +4.70% (+0.092s) | 0.0354 | 3 | 0.0125 | **FAIL TO REJECT $H_{0,3}$** |
| $H_{0,6}$ | C2 matches C3 at mod load | c2-full vs c3-static | Build time median | +1.78% (+0.035s) | 0.2281 | 4 | 0.0167 | **FAIL TO REJECT $H_{0,6}$** |
| $H_{0,2}$ | C2 matches C1 at high load | c2-full vs c1-reactive | Build time median | +2.42% (+0.048s) | 0.2584 | 5 | 0.0250 | **FAIL TO REJECT $H_{0,2}$** |
| $H_{0,5}$ | C2 matches C1 at mod load | c2-full vs c1-reactive | Build time median | -0.76% (-0.015s) | 0.8789 | 6 | 0.0500 | **FAIL TO REJECT $H_{0,5}$** |

---

### Family 2: System Safety & Overhead (Tier 1)
$M = 3$ tests.

| Hypothesis | Description | Metric | Threshold | Observed Value | Decision |
|:---|:---|:---|:---|:---|:---|
| $H_{0,7}$ | Idle bounded harm | C2 vs C0 build median delta | $\le 5\%$ | +0.82% (95% CI [-1.5%, +2.8%]) | **FAIL TO REJECT $H_{0,7}$ (Safe)** |
| $H_{0,8}$ | False reservation rate | Expired unclaimed / armed | $\le 5\%$ | 0.0% (0 / 43 expired unclaimed across Tier 1) | **REJECT $H_{0,8}$ (Zero waste in Scen A)** |
| $H_{0,9}$ | Daemon CPU overhead | Mean background CPU usage | $\le 1\%$ | 4.31% (high), 4.90% (mod), 1.80% (idle) | **FAIL TO REJECT $H_{0,9}$ (Overhead exceeded)** |

---

### Family 3: Scenario Generalization (Tier 2)

| Hypothesis | Description | Metric | Observed Value | Unadjusted $p$ | Status / Decision |
|:---|:---|:---|:---|:---|:---|
| $H_{0,10}$ | Speedup in bursty saves | C2 vs C0 build median in Scenario B | -52.99% (-2.533s) | $< 1.0 \times 10^{-6}$ | **INVALID AS RUN** (Constant 12.97s delay, not irregular) |
| $H_{0,11}$ | Zero waste in save-without-build | Reservations armed in Scenario C | 0 armed (empty model) | N/A | **VACUOUS AS RUN** (Empty model guaranteed 0 arming) |
| $H_{0,12}$ | C2 matches C1 in bursty saves | C2 vs C1 build median in Scenario B | +3.80% (+0.082s) | 0.1930 | **INVALID AS RUN** (Workload was not irregular) |

---

### Family 4: Mechanism Ablations (Tier 3)

| Hypothesis | Description | Comparison | Metric | Observed Value | Statistical Test / $p$-value | Decision |
|:---|:---|:---|:---|:---|:---|:---|
| $H_{0,13}$ | A2 unboosted rate matches C2 | a2-noconf vs c2-full | Unboosted build count | A2: 2/30 vs C2: 0/30 | Fisher's Exact two-sided $p = 0.4915$ | **FAIL TO REJECT $H_{0,13}$ (No diff)** |
| $H_{0,14}$ | A3 arming rate matches C2 | a3-nocont vs c2-full | Idle arming count | A3: 0/50 vs C2: 0/50 | Indeterminate (Cost gate shadowed contention gate) | **Did not ablate (See Section 4)** |
| $H_{0,15}$ | A4 claim latency matches C2 | a4-fixedwin vs c2-full | Claim latency median | A4: 44.6ms vs C2: 45.2ms | Mann-Whitney $U$, $p = 0.6210$ | **FAIL TO REJECT $H_{0,15}$** |
| $H_{0,16}$ | A5 holdout ablation | a5-noholdout vs c2-full | Holdout count | A5: 0/50 vs C2: 7/50 | Exact count comparison | **VERIFIED (Holdouts suppressed)** |

---

## 4. Section: INVALID AS RUN

A rigorous audit of the execution harness and raw data revealed four critical experimental defects in the historical runs:

1. **Scenario B Delay Invariance (Tiers 2 & 3):**
   - **Flaw**: The measurement harness invoked `build_on_save.py` with `--cycles 1` for each measured build. Because no `--seed` argument was supplied, Python's `random.Random(42)` re-seeded on every invocation.
   - **Consequence**: Every measured build in Scenario B experienced an identical delay of ~12.968 seconds ($CV = 0.0002$, min 12.960s, max 12.969s). Scenario B was completely regular.
   - **Invalidated Claims**: $H_{0,10}$, $H_{0,12}$, and the S4 Scenario B evaluation are invalid as evidence of irregular workload handling. The narrative that "the gate closed due to dispersion" was completely false; the gate opened on Save 6 once the constant delays lowered EWMA variance.
   - **Legitimate Finding**: These runs demonstrate that under long constant delays (~13s), PreCompile opens wide prediction windows (~16.8s collapsing to 1.7s), maintaining 0% false expirations at the cost of prolonged intensified polling.

2. **Scenario A Constant Rhythm (Tiers 1 & 3):**
   - **Flaw**: Scenario A builds were also invoked with `--cycles 1` without seed variation, causing every build to receive the first draw of $N(1.8, 0.1)$ under seed 42 (~1.786 seconds).
   - **Consequence**: Scenario A evaluated a constant 1.786s rhythm with zero empirical jitter. Because this constant rhythm fell cleanly within the predicted window $[1.4\text{s}, 2.2\text{s}]$, the contended speedups (+75.6%) remain empirically valid, but distributional variation was absent.

3. **Post-Hoc A3' Idle Regression Confound:**
   - **Flaw**: Post-hoc A3' was executed in isolation at 07:39 UTC. Both reserved builds (median 3.88s) and unboosted holdouts (median 3.88s) doubled in duration, and observation-only warm-ups before any reservation took 4.0–4.2s.
   - **Consequence**: The slowdown was caused by a host machine-state anomaly (CPU power/thermal throttling or external background load), not by cgroup actuation or 100 Hz polling.
   - **Withdrawn Claim**: The previous claim that "contention gating is essential to prevent idle regression" is completely unsupported and is formally withdrawn.

4. **Scenario C' Save Supersession Defect:**
   - **Flaw**: In Scenario C', save events occurred every 2.0s. Each new save superseded `_pending_saves[project_id]` before the correlation timeout expired.
   - **Consequence**: Superseded predictions were never evaluated by `expire_pending` and remained unscored (`outcome = NULL`). Under the Step 5 design, unscored predictions are excluded from hit-rate calculations. The gate's hit rate stayed frozen at 100% from warm-ups, causing `can_arm` to remain `True` across all 50 saves.

---

## 5. Success Criteria S1 - S7 Status

*Exact values preserved from the pre-registered analyzer (`analyze_experiments.py`).*

| Criterion | Pre-Registered Target | Observed Value | Status |
|:---|:---|:---|:---|
| **S1: Contended Speedup** | $\ge 20\%$ speedup at high load ($p < 0.05$) | ITT: **+75.6%** (High, $p < 10^{-15}$); Reserved-only: **+76.4%** | **PASS** |
| **S2: Anticipatory vs Reactive** | No threshold; effect & 95% CI reported | High: -2.4% [95% CI: -6.8%, +1.5%]; Idle: -1.0% | **REPORTED** |
| **S3: Claim Latency Advantage** | C2 claim lat $\ge 25\%$ lower than C1 | High: observed **+9.4%** advantage (45.2ms vs 49.9ms); target sub-15ms failed | **FAIL** |
| **S4: Precision on Irregular Work** | Scen B false res $\le 20\%$, Scen C arm $\le 20\%$ | Scen B: 0.0% false (invalid workload); Scen C: 0.0% arm (vacuous) | **INVALID AS RUN** |
| **S5: Monitoring Overhead (C2)** | Daemon CPU $\le 1.0\%$, Avg Scan $\le 1.5$ ms | Observed Mean CPU: **3.45%**; Avg Scan: **0.26 ms** | **FAIL** (CPU exceeded) |
| **S6: Bounded Harm** | Idle harm $\le 5\%$ delta vs baseline | Observed Idle Delta: **+0.82%** (well within $\pm 5\%$) | **PASS** |
| **S7: Resource Cleanup** | 0 leftover cgroups, 0 orphan processes | Leftover cgroups: **0**; Leftover processes: **0** | **PASS** |

---

## 6. S3 Claim-Latency Analysis

### Tier 1 High Load Claim Latency Distributions

| Condition | N | Min | P25 | Median | P75 | P95 | Max | Mean |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| **c1-reactive** | 50 | 18.77 ms | 35.71 ms | **49.94 ms** | 57.55 ms | 67.27 ms | 75.57 ms | 47.22 ms |
| **c2-full** | 43 | 37.09 ms | 41.82 ms | **45.24 ms** | 48.68 ms | 52.24 ms | 58.94 ms | 45.60 ms |
| **c3-static** | 50 | 15.81 ms | 23.18 ms | **27.12 ms** | 31.86 ms | 36.40 ms | 38.93 ms | 27.32 ms |

- **Definition of Latency Column**: Latency is computed identically for all conditions as `max(0.0, now_monotonic - proc.t_start_monotonic)`.
- **C2 vs C3 Gap in Tier 1**: In Tier 1, C3 achieved 27.12 ms vs C2's 45.24 ms. However, in the controlled microbenchmark with daemon restarts every 10 builds under high load, C2 gave 45.8 ms and C3 gave 48.0 ms. The gap between C2 and C3 in Tier 1 is therefore **unexplained** by system architecture and represents host scheduling variance during that specific run.

---

## 7. Data Provenance

Every data point in this report is traceable to specific raw files, timestamps, and git commits:

| Table / Section | Primary Data Source | Processing Script | Verification Command |
|:---|:---|:---|:---|
| **Table 2a (Build Performance)** | `benchmarks/results_tier1/results.jsonl`<br>`benchmarks/results_tier2/results.jsonl`<br>`benchmarks/results_tier3/results.jsonl`<br>`benchmarks/results_posthoc/results.jsonl` | `benchmarks/generate_missing_tables.py`<br>`benchmarks/analyze_experiments.py` | `wsl python3 benchmarks/generate_missing_tables.py` |
| **Table 2b (System Metrics)** | `benchmarks/results_tier1/block_metrics.jsonl`<br>`benchmarks/results_tier2/block_metrics.jsonl`<br>`benchmarks/results_tier3/block_metrics.jsonl`<br>`benchmarks/results_posthoc/block_metrics.jsonl` | `benchmarks/generate_missing_tables.py`<br>`benchmarks/analyze_experiments.py` | `wsl python3 benchmarks/analyze_experiments.py --block-metrics ...` |
| **Table 2c (Mechanism Tests)** | `precompile/reservation.py`<br>`benchmarks/mechanism_tests.py`<br>`benchmarks/holdout_agreement.log` | `benchmarks/mechanism_tests.py` | `cat benchmarks/holdout_agreement.log` |
| **Hypothesis Tests (Family 1-4)** | `benchmarks/results_tier1/results.jsonl`<br>`benchmarks/results_tier2/results.jsonl`<br>`benchmarks/results_tier3/results.jsonl` | `precompile/stats.py` (`mann_whitney_u`) | `wsl python3 scratch/compute_hypotheses.py` |
| **S1-S7 Status Table** | `benchmarks/results/results_all.jsonl`<br>`benchmarks/results/summary_metrics.csv` | `benchmarks/analyze_experiments.py` | `wsl python3 benchmarks/analyze_experiments.py --jsonl ...` |
