# PreCompile Evaluation: Pre-Registered Experiment Plan

**Document Version:** 1.2.0  
**Registration Timestamp:** 2026-10-20T16:15:00Z  
**Previous Registrations:**  
- Version 1.1.0 (commit `0a4d2cb`, 2026-10-20T10:45:00Z)  
- Version 1.0.0 (commit `6bca5fc`, 2026-10-20T09:16:47Z)  
**Stage Mapping:** Step 11a / Step 11b (System Design Specification Stage 11)  
**Execution Environment:** WSL2 Linux 6.18.33.2-microsoft-standard-WSL2 on 13th Gen Intel(R) Core(TM) i7-1355U (12 vCPUs, 7.6 GiB RAM)  
**Transparency Statement:** This amended plan (Version 1.2.0) incorporates empirical insights from the exploratory Pilot 2 run prior to the execution of final Tier 1 and Tier 2 evaluations. Specifically, Pilot 2 revealed:
1. **Measurement Definition Disparity:** The C0 unboosted baseline duration erroneously measured wrapper process wall time (including the pre-build delay of 1.8s in Scenario A and 0.5–20.0s in Scenario B) because no daemon ran to populate SQLite process lifetimes, whereas boosted conditions reported daemon-side compiler lifetimes. Plan v1.2 establishes a single canonical monotonic duration metric (`build_wall_s`) measured strictly around `make` across all conditions.
2. **Success Criterion S5 Multi-Part Enforcement:** In Pilot 2, the reporting table marked S5 PASS based solely on average scan time ($\le 1.5$ ms) while daemon CPU exceeded the 1.0% core limit. Plan v1.2 strictly mandates that S5 fails if either daemon CPU $> 1.0\%$ OR average scan time $> 1.5$ ms.
3. **Scenario Database Isolation Defect:** Database state was maintained across scenarios, causing Scenarios B and C to operate on models trained on Scenario A's delay and reading cumulative metrics (Scenario C showed 100% hit rate with no builds). Plan v1.2 mandates strictly isolated per-cell databases (`db_{sc}_{ld}_{cn}.db`) and block-scoped metric querying.
4. **Flag Semantics & Cell Keying:** Unboosted builds were diluted by builds where the contention gate correctly abstained (e.g. idle builds). Plan v1.2 decouples `unboosted` (intended boost that failed) from `abstained` (gate correctly closed), and keys analysis cells by `(tier, scenario, load, condition)`.

---

## 1. Experimental Conditions

The evaluation suite investigates the full operational spectrum of PreCompile alongside baseline alternatives and targeted ablations:

### Primary Baseline Conditions
1. **C0 (Unboosted Baseline - No Daemon):** No PreCompile daemon running at all. Build processes execute in the default user cgroup (`/sys/fs/cgroup/init.scope` with default `cpu.weight = 100`). Measures natural contention slowdown without any observation, polling, or scheduling intervention.
2. **C1 (Reactive Boost):** Process-spawn reactive boosting. No anticipatory reservation on save. When a matching build process is discovered in `/proc`, if system CPU PSI $\ge 10.0\%$, PreCompile creates a reservation cgroup (`cpu.weight = 500`) and migrates the process tree.
3. **C2 (Full Anticipatory Pre-Reservation):** Full PreCompile system. Confidence-gated, contention-gated, learned EWMA save-to-build lead/lag distribution, anticipatory cgroup arming ahead of process spawn, atomic claim upon process detection, randomized holdouts ($p = 0.10$), and closed-loop benefit estimation.
   - **Intention-to-Treat Protocol:** In primary hypothesis testing, every build executed under C2 counts as a C2 build regardless of whether it was randomized into the holdout arm. Counterfactual holdouts are an intrinsic component of C2's self-measurement. Secondary reporting provides decomposed reserved-only and holdout-only medians.
4. **C3 (Static Priority Reservation):** Always-on elevated reservation cgroup (`cpu.weight = 500`) with continuously intensified watcher (10 ms polling interval). Simulates traditional static priority boosting without temporal or contention gating.

### Scenario B Interpretation Note
In exploratory pilot data, C2 made 0 claims in 5 builds under Scenario B (manual irregular delays $U(0.5, 20.0)\text{ s}$) and was ~2.3x slower than C1 (4.58 s vs 1.98 s). This occurs because when the save-to-build delay distribution is highly disperse and non-stationary, the confidence gate correctly abstains, and PreCompile falls back to default Linux CFS scheduling (`cpu.weight = 100`). This behavior is directly expected from the IDF design (abstain $\to$ default scheduling). A reactive fallback mechanism that boosts unarmed builds if contention is detected after process spawn is a possible extension, listed strictly as **FUTURE WORK** and **NOT** implemented or evaluated in this study.

### Ablation Conditions (System Components)
5. **A1 (Ablation: Reactive):** Functional alias for C1, evaluated directly within the ablation matrix.
6. **A2 (Ablation: No Confidence Gate):** Always arms on file save regardless of whether the predictor has accumulated minimum sample size ($n < 5$) or met accuracy thresholds.
7. **A3 (Ablation: No Contention Gate):** Arms on file save whenever the confidence gate passes, ignoring system CPU PSI (`/proc/pressure/cpu`).
8. **A4 (Ablation: Fixed Window):** Arms using a fixed lead/lag window ($[0.0\text{s}, 5.0\text{s}]$) rather than the learned EWMA dispersion distribution $[\hat{\mu} - 2\hat{\sigma}, \hat{\mu} + 2\hat{\sigma}]$.
9. **A5 (Ablation: No Holdout):** Sets holdout probability to 0.0 ($100\%$ reservation rate), disabling counterfactual holdouts and benefit controller feedback.

---

## 2. Workload Scenarios & Load Levels

The evaluation scenarios match IDF Section 8 and the workload simulator ([build_on_save.py](file:///c:/Os%20Project/benchmarks/build_on_save.py)):

### Evaluated Scenarios
- **Scenario A (Automatic Build after Stable Delay):** Developer editor saves trigger automated builds after a consistent, short lead time (`build_on_save --mode auto`, delay $\sim \mathcal{N}(1.8, 0.1^2)\text{ s}$, clamped $\ge 0.01\text{ s}$). Workload is a single-threaded rebuild of a heavy C translation unit (`big.c`, 25,000 synthetic AST nodes / statements, ~7,450 lines). Evaluated across Idle, Moderate, and High load levels.
- **Scenario B (Manual Builds at Irregular Delays):** Developer performs edits followed by manual compile commands at highly variable intervals (`build_on_save --mode manual-irregular`, delay uniformly distributed $U(0.5, 20.0)\text{ s}$). Evaluated at Moderate load (30 builds per condition, 3 blocks of 10 builds).
- **Scenario C (Save-Only Workload):** Developer performs repetitive file saves without triggering any build command (`build_on_save --mode save-only`, 2.0 s inter-save interval). Evaluates reservation abstention and false reservation prevention under Moderate load (50 saves per condition).

### Synthetic Contention Load Levels
Background CPU load is generated using `stress-ng` pinned to the identical CPU set as the build workload (`CPUSET=0-1`, 2 logical cores):
- **Idle:** 0 background workers (clean uninhibited execution).
- **Moderate:** 2 CPU stress hogs per assigned CPU core (`stress-ng --cpu 4 --taskset 0-1`). Induces moderate CPU PSI contention ($\sim 25\% - 45\%$).
- **High:** 4 CPU stress hogs per assigned CPU core (`stress-ng --cpu 8 --taskset 0-1`). Induces severe CPU PSI contention ($\sim 60\% - 95\%$).

---

## 3. Block Rotation Protocol, Scenario Isolation & Safety Rules

To eliminate thermal throttling artifacts, OS page cache drift, and chronological ordering bias:
- **Latin Square / Block Rotation:** Experiments execute in rotating blocks across conditions:
  $$\text{Block Sequence: } [C_0, C_1, C_2, C_3] \to [C_1, C_2, C_3, C_0] \to [C_2, C_3, C_0, C_1] \to [C_3, C_0, C_1, C_2] \dots$$
- **Block Size:** 10 measured builds per condition block.
- **Cycles:** 5 full cycles per condition = **50 measured builds per condition** (10 per block $\times$ 5 blocks).
- **Cell-Isolated Databases:** Each cell `(condition, scenario, load_level)` maintains an isolated database `db_{sc}_{ld}_{cn}.db`. Learned state starts completely empty at Cycle 1 of that cell and is preserved across blocks belonging strictly to that cell. No cross-scenario data leakage occurs.
- **Warm-Ups:** Each condition executes **12 unmeasured warm-up builds** on Cycle 1 prior to starting measured blocks. Warm-ups strictly follow the cell's scenario distribution (e.g. irregular delays for Scenario B). Warm-ups are counted separately and excluded from measured evaluation results.
- **Block-Scoped Database Metrics:** All metrics derived from SQLite tables (`hit_rate`, `false_res_rate`, `missed_rate`) are strictly scoped using row ID watermarks captured after warm-ups complete at the start of measured execution.
- **Analysis Cell Keying:** Analysis groups records by `(tier, scenario, load_level, condition)`. Special ramp-down or diagnostic blocks are tagged separately (e.g. `tier="rampdown"`) and are never merged into the main experimental cells.
- **PSI Ramp-Up Settling Delay:** Following synthetic load escalation (`load.sh moderate` or `load.sh high`), the harness enforces a **12-second settling pause** for `/proc/pressure/cpu` 10-second rolling averages to stabilize.
- **PSI Ramp-Down Rule:** After any decrease in load, the harness monitors `/proc/pressure/cpu` and blocks until `some_avg10 < 5.0%` (or at most 40.0 s). Blocks at Idle MUST start with `some_avg10 < 5.0%`.
- **Watchdog Timeout:** Each individual build is guarded by a hard 60.0 s watchdog timeout.
- **Registration Discipline & Dirty-Tree Check:** The harness queries git status and records `git rev-parse HEAD`, `git_dirty`, and whether `EXPERIMENT_PLAN.md` differs from HEAD in `harness_header.json`. The harness refuses to start a non-pilot Tier 1 or Tier 2 run if the working tree is dirty unless `--force` is explicitly provided.

---

## 4. Evaluated Metrics & Canonical Duration Definition

1. **Canonical Build Duration ($T_{\text{wall}}$ / `build_wall_s`):**  
   The authoritative build duration is measured by `build_on_save.py` strictly around the `make` subprocess invocation using Python's high-resolution monotonic clock:
   $$T_{\text{wall}} = t_{\text{make\_end}} - t_{\text{make\_start}}$$
   This definition applies identically to every condition (including C0 where no daemon runs). Recorded in JSONL as `build_wall_s` and used for all statistical comparisons.
2. **Daemon-Side Duration Cross-Check (`daemon_duration_s`):**  
   For boosted conditions, the daemon-side process lifetime ($t_{\text{exit}} - t_{\text{start}}$) is recorded separately as `daemon_duration_s`. The absolute difference $|build\_wall\_s - daemon\_duration\_s|$ is tracked per build, and its distribution (min, p25, median, p75, max, mean) is reported as a measurement validity invariant.
3. **Process CPU Time ($T_{\text{cpu}}$):** Cumulative user + system CPU seconds consumed by the build process tree, read via child process resource usage (`ru_utime + ru_stime`).
4. **Cgroup CPU Share ($S_{\text{cpu}}$):** Ratio of reservation group CPU time to elapsed wall time:
   $$S_{\text{cpu}} = \frac{\Delta \text{usage\_usec}}{10^6 \times \Delta t_{\text{wall}}}$$
5. **Unboosted vs. Abstained Flags:**
   - **`unboosted`:** Set to True only when a boost was intended (armed reservation, or gate open with claim expected) and $S_{\text{cpu}} < 0.50$, late claim ($> 100$ ms), or missed claim.
   - **`abstained`:** Set to True when the contention gate or confidence gate correctly abstained (e.g. idle conditions, PSI $< 10\%$). These builds are labeled `"not boosted (abstained)"` and are not counted as unboosted. Both counts are reported in summary tables.
6. **Opt-in Descendant Probes & Weight Readbacks:**
   - For boosted conditions, a background thread logs descendant process counts and comm names outside the reservation group at claim $+0.5\text{ s}$ and $+1.5\text{ s}$.
   - Group `cpu.weight` is read back from the filesystem at claim and close, along with `cpu.stat`.
7. **Daemon CPU Percentage ($P_{\text{daemon}}$) & Per-Thread Profiling:**
   Total daemon CPU % is computed from `/proc/<pid>/stat`. Additionally, per-thread CPU time is read from `/proc/<pid>/task/*/stat` to profile watcher, housekeeping, exit monitor, file monitor, and sweeper thread shares.
8. **Claim Latency ($L_{\text{claim}}$):** Elapsed duration in milliseconds from process discovery in `/proc` to cgroup migration completion.
9. **Watcher Scan Frequency and Duration:** Total `/proc` scans, scans per second ($f_{\text{scan}}$), and average scan duration ($\bar{t}_{\text{scan}}$ in ms).
10. **Background Impact ($I_{\text{bg}}$):** Aggregate CPU seconds per wall-clock second consumed by `stress-ng` background worker threads.

---

## 5. Statistical Testing & Significance Control

### Primary Null Hypotheses ($H_0$)
For Scenario A at each contested load level (Moderate, High), family-wise error rate is controlled across $m = 3$ tested hypotheses ($m = 6$ combined):
1. **$H_{0,1}$ (Contended Speedup vs Unboosted):** $T_{\text{wall}}(C_2) \ge T_{\text{wall}}(C_0)$ in Scenario A.
2. **$H_{0,2}$ (Anticipatory vs Reactive Duration):** $T_{\text{wall}}(C_2) = T_{\text{wall}}(C_1)$ in Scenario A.
3. **$H_{0,3}$ (Claim Latency Advantage):** $L_{\text{claim}}(C_2) \ge L_{\text{claim}}(C_1)$.

### Hypothesis Testing Protocol
- **Test Statistic:** Two-sided Mann-Whitney U test with exact tie correction across independent blocks.
- **Family-Wise Error Rate (FWER):** Controlled at $\alpha = 0.05$ using step-down **Holm-Bonferroni correction**.
- **Confidence Intervals:** 95% non-parametric bootstrap confidence intervals (1,000 resamples).
- **Primary vs. Sensitivity S3 Testing:**
  - **Primary Test:** S3 is evaluated on **ALL builds**. S3 PASS is declared if and only if the primary test achieves $\ge 25.0\%$ latency reduction with $p_{\text{Holm}} < 0.05$.
  - **Sensitivity Analysis:** The variant excluding build index 0 is reported as a labeled sensitivity analysis (`S3 Sensitivity (ex-idx0)`).

---

## 6. Pre-Registered Success Criteria (S1–S7)

| Criterion | Definition & Metric | Target Requirement | Pre-Declared Assessment & Fallback Rule |
| :--- | :--- | :--- | :--- |
| **S1: Contended Speedup** | $T_{\text{wall}}(C_2)$ vs $T_{\text{wall}}(C_0)$ in Scenario A (Moderate & High load) | Relative median reduction $\ge 20.0\%$ with Holm-corrected $p < 0.05$ | Core speedup benchmark evaluated with canonical monotonic duration `build_wall_s`. |
| **S2: Anticipatory vs Reactive** | $T_{\text{wall}}(C_2)$ vs $T_{\text{wall}}(C_1)$ in Scenario A (Moderate & High load) | Report effect and 95% bootstrap CI; **No artificial threshold** | Anticipatory vs reactive build-time delta. If no significant difference, assessed via S3, S4, S5, S6 per IDF Section 8. |
| **S3: Claim Latency** | $L_{\text{claim}}(C_2)$ vs $L_{\text{claim}}(C_1)$ in Scenario A (High load) | $C_2$ median claim latency at least $25.0\%$ lower than $C_1$ ($p_{\text{Holm}} < 0.05$) on ALL builds | Primary test evaluated across all builds. Ex-idx0 is a labeled sensitivity analysis. |
| **S4: Precision on Irregular Workloads** | $R_{\text{false}}$ in Scenario B, Arming Rate in Scenario C | $R_{\text{false}}(C_2) \le 20.0\%$ in Scenario B; Reservations armed $\le 20.0\%$ in Scenario C | Assesses precision on irregular delays. Evaluated on block-scoped rows. |
| **S5: Monitoring Overhead** | Daemon CPU % ($P_{\text{daemon}}$) and average scan time ($\bar{t}_{\text{scan}}$) | Requires BOTH $P_{\text{daemon}} \le 1.0\%$ of one core AND $\bar{t}_{\text{scan}} \le 1.5\text{ ms}$ | Fails if either part exceeds threshold. Table reports both values. |
| **S6: Bounded Harm & Safety** | Idle arming rate, idle build duration delta, background hog throughput | Idle arming rate $\le 10.0\%$; boosted idle $T_{\text{wall}}$ within $\pm 5.0\%$ of $C_0$; hog impact of $C_2$ not worse than $C_3$ | Verified via idle sanity checks and ramp-down settlement. |
| **S7: Resource Cleanup** | Lingering cgroups and orphan processes | Exactly 0 leftover `precompile-res-*` cgroups and 0 orphaned background processes | Verified after every block and evaluation conclusion. |

---

## 7. Execution Time Arithmetic

Based on empirical pilot build durations and the block protocol:

### Tier 1 (Scenario A: 4 Conditions $\times$ 3 Loads $\times$ 5 Cycles = 60 Blocks)
- **Measured Builds:** 60 blocks $\times$ 10 builds = 600 builds.
- **Warm-Up Builds:** 12 conditions/loads $\times$ 12 builds on Cycle 1 = 144 builds.
- **Mean Pure Build Duration:** High: $C_0 \approx 8.3\text{s}$, $C_1/C_2/C_3 \approx 2.0\text{s}$; Moderate: $C_0 \approx 4.8\text{s}$, Boosted $\approx 2.0\text{s}$; Idle: $\approx 1.8\text{s}$. Mean build time: $\approx 3.0\text{ s}$.
- **Pure Measured Build Time:** 600 builds $\times 3.0\text{ s} = 1,800\text{ s}$ (30.0 min).
- **Inter-build Delay (1.8s delay + gap):** 600 builds $\times 3.8\text{ s} = 2,280\text{ s}$ (38.0 min).
- **Warm-Up Time:** 144 builds $\times \sim 3.8\text{ s} = 547\text{ s}$ (9.1 min).
- **PSI Ramp-Up Waits:** 2 load transitions $\times 12\text{s} = 24\text{ s}$.
- **PSI Ramp-Down Waits:** 1 load transition $\times 35\text{s} = 35\text{ s}$.
- **Daemon Lifecycle Overhead:** 60 blocks $\times 1.5\text{s} = 90\text{ s}$.
- **Estimated Tier 1 Wall Time:** $\approx \mathbf{4,776\text{ s}}$ (**~79.6 minutes / ~1.33 hours**).

### Tier 2 (Scenario B & Scenario C at Moderate Load)
- **Scenario B (Irregular Delays):** 4 conditions $\times$ 30 builds = 120 builds across 12 blocks.
  - Mean delay: $10.25\text{ s}$, mean build time: $2.5\text{ s} \implies 12.75\text{ s}$ per build.
  - Total Scenario B: $120 \times 12.75\text{ s} = 1,530\text{ s}$ (25.5 min).
- **Scenario C (Save-Only):** 4 conditions $\times$ 50 saves = 200 saves.
  - Interval: $2.0\text{ s}$ per save $\implies 200 \times 2.0\text{s} = 400\text{ s}$ (6.7 min).
- **Estimated Tier 2 Wall Time:** $1,530 + 400 = \mathbf{1,930\text{ s}}$ (**~32.2 minutes / ~0.54 hours**).

### Combined Evaluation Wall Time
$$\text{Total Wall Time} = 4,776\text{ s} + 1,930\text{ s} = \mathbf{6,706\text{ s}} \quad (\mathbf{\sim 111.8\text{ minutes / 1.86 hours}})$$
