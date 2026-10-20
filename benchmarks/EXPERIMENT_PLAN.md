# PreCompile Evaluation: Pre-Registered Experiment Plan

**Document Version:** 1.3.0  
**Registration Timestamp:** 2026-10-20T19:36:09Z  
**Previous Registrations:**  
- Version 1.2.0 (commit `4051943`, 2026-10-20T16:22:34Z)  
- Version 1.1.0 (commit `0a4d2cb`, 2026-10-20T10:45:00Z)  
- Version 1.0.0 (commit `6bca5fc`, 2026-10-20T09:16:47Z)  
**Stage Mapping:** Step 11a / Step 11b (System Design Specification Stage 11)  
**Execution Environment:** WSL2 Linux 6.18.33.2-microsoft-standard-WSL2 on 13th Gen Intel(R) Core(TM) i7-1355U (12 vCPUs, 7.6 GiB RAM)  
**Transparency Statement:** This amended plan (Version 1.3.0) incorporates empirical insights and operational corrections discovered during Validation Pilot 3 prior to the execution of Tier 1, Tier 2, and Tier 3 evaluations:
1. **Warm-Until-Armed Protocol:** In Pilot 3, initializing a fresh per-cell database revealed that setting warm-ups to 2 in pilot mode prevented the confidence gate from accumulating the required $n \ge 5$ delay samples and $\ge 5$ scored predictions, leaving the gate closed during measured builds. Plan v1.3 introduces the authoritative `WARM-UNTIL-ARMED` protocol: for C2 and all ablations, warm-up builds continue until the gate is open ($n \ge 5$, scored predictions $\ge 5$, and `can_arm=True` read directly from the cell DB), with a minimum of 12 builds and a maximum of 30 builds. If the gate remains closed after 30 builds, the cell aborts. For Scenario B, warm-up runs 12 irregular builds and continues with whatever the gate decides. In pilot mode, the arbitrary 2-warm-up setting is removed and the identical rule applies.
2. **Flag Semantics & Decision Scoping:** Pilot 3 revealed that unboosted builds were overcounted when the gate was closed due to confidence. Plan v1.3 mandates that builds are labelled strictly from the stored per-build decision: holdout $\to$ `"holdout"`; gate closed or abstained (confidence, contention, cost, budget) $\to$ `"abstained"`; armed but not claimed, claimed late ($> 100\text{ ms}$), or $S_{\text{cpu}} < 0.50$ $\to$ `"unboosted"`; claimed with $S_{\text{cpu}} \ge 0.50$ $\to$ `"boosted"`; C0 baseline $\to$ `"observed"`. Block-scoped decision counts from `predictions` rows are explicitly reported in the block table.
3. **Per-Condition S5 Reporting:** Daemon CPU % (mean and max) and average scan duration (ms) are evaluated and reported separately for each condition running a daemon, with C2 designated as the primary evaluation condition. Success criterion S5 ($P_{\text{daemon}} \le 1.0\%$ AND $\bar{t}_{\text{scan}} \le 1.5\text{ ms}$) remains unchanged.
4. **Idle Order Effect Resolution:** A block-randomized 15-build sanity check (seed 42) resolved the earlier ~4% difference between C0 and C1 at idle, showing that it was an order artifact of sequential block execution. Under randomized block order, C0 vs C1 speedup was $-0.67\%$ ($p=0.6041$, 95% CI $[-4.3\%, +6.4\%]$), and C0 vs C3 was $+1.86\%$ ($p=0.1914$, 95% CI $[-2.4\%, +7.8\%]$), confirming bounded harm ($\le \pm 5.0\%$).
5. **Tier 3 (Ablations Matrix):** Formal specification of Tier 3 targeting each ablation where it matters: A2 (no confidence gate) on Scenario B at moderate load (30 builds); A3 (no contention gate) on Scenario A at idle (50 builds); A4 (fixed window) on Scenario A at high load (50 builds); A5 (no holdout) on Scenario A at high load (50 builds). Hypotheses and Holm families are pre-registered below. Success criteria S1–S7 remain unchanged.

---

## 1. Experimental Conditions

The evaluation suite investigates the full operational spectrum of PreCompile alongside baseline alternatives and targeted ablations:

### Primary Conditions (Tier 1 & Tier 2)
1. **C0 (Unboosted Baseline - No Daemon):** No PreCompile daemon running at all. Build processes execute in the default user cgroup (`/sys/fs/cgroup/init.scope` with default `cpu.weight = 100`). Measures natural contention slowdown without any observation, polling, or scheduling intervention.
2. **C1 (Reactive Boost):** Process-spawn reactive boosting. No anticipatory reservation on save. When a matching build process is discovered in `/proc`, if system CPU PSI $\ge 10.0\%$, PreCompile creates a reservation cgroup (`cpu.weight = 500`) and migrates the process tree.
3. **C2 (Full Anticipatory Pre-Reservation):** Full PreCompile system. Confidence-gated, contention-gated, learned EWMA save-to-build lead/lag distribution, anticipatory cgroup arming ahead of process spawn, atomic claim upon process detection, randomized holdouts ($p = 0.10$), and closed-loop benefit estimation.
   - **Intention-to-Treat Protocol:** In primary hypothesis testing, every build executed under C2 counts as a C2 build regardless of whether it was randomized into the holdout arm. Counterfactual holdouts are an intrinsic component of C2's self-measurement. Secondary reporting provides decomposed reserved-only and holdout-only medians.
4. **C3 (Static Priority Reservation):** Always-on elevated reservation cgroup (`cpu.weight = 500`) with continuously intensified watcher (10 ms polling interval). Simulates traditional static priority boosting without temporal or contention gating.

### Scenario B Interpretation Note
In exploratory pilot data, C2 made 0 claims in 5 builds under Scenario B (manual irregular delays $U(0.5, 20.0)\text{ s}$) and was ~2.3x slower than C1 (4.58 s vs 1.98 s). This occurs because when the save-to-build delay distribution is highly disperse and non-stationary, the confidence gate correctly abstains, and PreCompile falls back to default Linux CFS scheduling (`cpu.weight = 100`). This behavior is directly expected from the IDF design (abstain $\to$ default scheduling). A reactive fallback mechanism that boosts unarmed builds if contention is detected after process spawn is a possible extension, listed strictly as **FUTURE WORK** and **NOT** implemented or evaluated in this study.

### Ablation Conditions (Tier 3 Matrix)
5. **A1 (Ablation: Reactive):** Functional alias for C1, evaluated directly within the ablation matrix.
6. **A2 (Ablation: No Confidence Gate):** Evaluated on Scenario B at Moderate Load (30 builds, 3 blocks of 10). Always arms on file save regardless of whether the predictor has accumulated minimum sample size ($n < 5$) or met accuracy thresholds.
7. **A3 (Ablation: No Contention Gate):** Evaluated on Scenario A at Idle Load (50 builds, 5 blocks of 10). Arms on file save whenever the confidence gate passes, ignoring system CPU PSI (`/proc/pressure/cpu`).
8. **A4 (Ablation: Fixed Window):** Evaluated on Scenario A at High Load (50 builds, 5 blocks of 10). Arms using a fixed lead/lag window ($[0.0\text{s}, 5.0\text{s}]$) rather than the learned EWMA dispersion distribution $[\hat{\mu} - 2\hat{\sigma}, \hat{\mu} + 2\hat{\sigma}]$.
9. **A5 (Ablation: No Holdout):** Evaluated on Scenario A at High Load (50 builds, 5 blocks of 10). Sets holdout probability to 0.0 ($100\%$ reservation rate), disabling counterfactual holdouts and benefit controller feedback.

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
- **Cell-Isolated Databases:** Each cell `(scenario, load_level, condition)` maintains an isolated database `db_{sc}_{ld}_{cn}.db`. Learned state starts completely empty at Cycle 1 of that cell and is preserved across blocks belonging strictly to that cell. No cross-scenario data leakage occurs.
- **WARM-UNTIL-ARMED Protocol:**
  - For C2 and ablations on Cycle 1, warm-ups continue until the gate opens: $n \ge 5$, scored predictions $\ge 5$, and `can_arm=True` (evaluated from the cell DB).
  - Minimum 12 warm-up builds; maximum 30 warm-up builds.
  - If the gate remains closed after 30 builds, the cell aborts and reports failure.
  - For Scenario B, warm-up runs 12 irregular builds and continues with whatever the gate decides.
  - Warm-ups are counted separately and excluded from measured evaluation results.
- **Block-Scoped Database Metrics:** All metrics derived from SQLite tables (`hit_rate`, `false_res_rate`, `missed_rate`, `decision_counts`) are strictly scoped using row ID watermarks captured after warm-ups complete at the start of measured execution.
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
5. **Classification and Flag Semantics:**
   - **`"holdout"`:** Build randomized into holdout arm (`is_holdout = True`, `unboosted = False`, `abstained = False`).
   - **`"abstained"`:** Contention gate or confidence gate correctly chose not to arm (e.g. idle conditions, PSI $< 10\%$, high variance $CV > 0.5$). `abstained = True`, `unboosted = False`. Labeled `"not boosted (abstained)"`.
   - **`"unboosted"`:** A priority boost was intended (armed reservation, or claim expected), but failed due to expiration before claim, late claim ($> 100\text{ ms}$), or throttling ($S_{\text{cpu}} < 0.50$). `unboosted = True`, `abstained = False`.
   - **`"boosted"`:** Process was claimed with $S_{\text{cpu}} \ge 0.50$ and latency $\le 100\text{ ms}$.
   - **`"observed"`:** Natural unboosted execution under C0 baseline.
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

### Hypotheses Families & Holm Correction
Family-wise error rate is controlled at $\alpha = 0.05$ using step-down **Holm-Bonferroni correction** within defined families:

#### Family 1: Scenario A, High Load (Tier 1: $m = 3$; with Tier 3: $m = 5$)
1. **$H_{0,1}$ (Contended Speedup vs Unboosted):** $T_{\text{wall}}(C_2) \ge T_{\text{wall}}(C_0)$ in Scenario A High.
2. **$H_{0,2}$ (Anticipatory vs Reactive Duration):** $T_{\text{wall}}(C_2) = T_{\text{wall}}(C_1)$ in Scenario A High.
3. **$H_{0,3}$ (Claim Latency Advantage):** $L_{\text{claim}}(C_2) \ge L_{\text{claim}}(C_1)$ in Scenario A High.
4. **$H_{0,4}$ (A4 Fixed Window Comparison):** $L_{\text{claim}}(C_2) \ge L_{\text{claim}}(A_4)$ in Scenario A High (Tier 3).
5. **$H_{0,5}$ (A5 No Holdout Comparison):** $T_{\text{wall}}(C_2) = T_{\text{wall}}(A_5)$ in Scenario A High (Tier 3).

#### Family 2: Scenario A, Moderate Load (Tier 1: $m = 2$)
1. **$H_{0,6}$ (Moderate Speedup vs Unboosted):** $T_{\text{wall}}(C_2) \ge T_{\text{wall}}(C_0)$ in Scenario A Moderate.
2. **$H_{0,7}$ (Anticipatory vs Reactive Duration):** $T_{\text{wall}}(C_2) = T_{\text{wall}}(C_1)$ in Scenario A Moderate.

#### Family 3: Scenario A, Idle Load (Tier 1: $m = 2$; with Tier 3: $m = 3$)
1. **$H_{0,8}$ (Idle Non-Inferiority vs C0):** $T_{\text{wall}}(C_2) > 1.05 \times T_{\text{wall}}(C_0)$ in Scenario A Idle.
2. **$H_{0,9}$ (Idle Comparison vs C1):** $T_{\text{wall}}(C_2) = T_{\text{wall}}(C_1)$ in Scenario A Idle.
3. **$H_{0,10}$ (A3 No Contention Gate Harm):** $\text{ArmRate}(A_3) \le \text{ArmRate}(C_2)$ at Idle (Tier 3).

#### Family 4: Scenario B, Moderate Load (Tier 2: $m = 2$; with Tier 3: $m = 3$)
1. **$H_{0,11}$ (Scenario B Duration vs C0):** $T_{\text{wall}}(C_2) = T_{\text{wall}}(C_0)$ in Scenario B.
2. **$H_{0,12}$ (Scenario B Precision):** $R_{\text{false}}(C_2) > 0.20$ in Scenario B.
3. **$H_{0,13}$ (A2 No Confidence Gate Precision):** $R_{\text{false}}(A_2) \le R_{\text{false}}(C_2)$ in Scenario B (Tier 3).

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
| **S5: Monitoring Overhead** | Daemon CPU % ($P_{\text{daemon}}$) and average scan time ($\bar{t}_{\text{scan}}$) | Requires BOTH $P_{\text{daemon}} \le 1.0\%$ of one core AND $\bar{t}_{\text{scan}} \le 1.5\text{ ms}$ | Fails if either part exceeds threshold. Evaluated per condition; C2 is primary. |
| **S6: Bounded Harm & Safety** | Idle arming rate, idle build duration delta, background hog throughput | Idle arming rate $\le 10.0\%$; boosted idle $T_{\text{wall}}$ within $\pm 5.0\%$ of $C_0$; hog impact of $C_2$ not worse than $C_3$ | Verified via idle sanity checks and ramp-down settlement. |
| **S7: Resource Cleanup** | Lingering cgroups and orphan processes | Exactly 0 leftover `precompile-res-*` cgroups and 0 orphaned background processes | Verified after every block and evaluation conclusion. |

---

## 7. Execution Time Arithmetic

Based on empirical pilot build durations and the block protocol:

### Tier 1 (Scenario A: 4 Conditions $\times$ 3 Loads $\times$ 5 Cycles = 60 Blocks)
- **Measured Builds:** 60 blocks $\times$ 10 builds = 600 builds.
- **Warm-Up Builds (Cycle 1):** 12 conditions/loads $\times$ 12 builds = 144 builds (with WARM-UNTIL-ARMED extending up to 30 if needed).
- **Mean Pure Build Duration:** High: $C_0 \approx 8.3\text{s}$, Boosted $\approx 2.0\text{s}$; Moderate: $C_0 \approx 4.8\text{s}$, Boosted $\approx 2.0\text{s}$; Idle: $\approx 1.8\text{s}$. Overall mean build time: $\approx 3.0\text{ s}$.
- **Pure Measured Build Time:** 600 builds $\times 3.0\text{ s} = 1,800\text{ s}$ (30.0 min).
- **Inter-build Delay (1.8s delay + subprocess launch):** 600 builds $\times 3.8\text{ s} = 2,280\text{ s}$ (38.0 min).
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

### Combined Tier 1 + Tier 2 Wall Time
$$\text{Total Wall Time} = 4,776\text{ s} + 1,930\text{ s} = \mathbf{6,706\text{ s}} \quad (\mathbf{\sim 111.8\text{ minutes / 1.86 hours}})$$
