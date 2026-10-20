# PreCompile Evaluation: Pre-Registered Experiment Plan

**Document Version:** 1.1.0  
**Registration Timestamp:** 2026-10-20T10:45:00Z  
**Previous Registration:** Version 1.0.0 (commit `6bca5fc`, 2026-10-20T09:16:47Z)  
**Stage Mapping:** Step 11a / Step 11b (System Design Specification Stage 11)  
**Execution Environment:** WSL2 Linux 6.18.33.2-microsoft-standard-WSL2 on 13th Gen Intel(R) Core(TM) i7-1355U (12 vCPUs, 7.6 GiB RAM)  
**Transparency Statement:** This amended plan (Version 1.1.0) was finalized AFTER initial exploratory pilot runs, smoke tests, and latency microbenchmarks had been observed. Criteria S1–S7 and scenario parameters are pre-registered strictly with respect to the main (unrun) experimental evaluation.

---

## 1. Experimental Conditions

The evaluation suite investigates the full operational spectrum of PreCompile alongside baseline alternatives and targeted ablations:

### Primary Baseline Conditions
1. **C0 (Unboosted Baseline - No Daemon):** No PreCompile daemon running at all. Build processes execute in the default user cgroup (`/sys/fs/cgroup/init.scope` with default `cpu.weight = 100`). Measures natural contention slowdown without any observation, polling, or scheduling intervention.
2. **C1 (Reactive Boost):** Process-spawn reactive boosting. No anticipatory reservation on save. When a matching build process is discovered in `/proc`, if system CPU PSI $\ge 10.0\%$, PreCompile creates a reservation cgroup (`cpu.weight = 500`) and migrates the process tree.
3. **C2 (Full Anticipatory Pre-Reservation):** Full PreCompile system. Confidence-gated, contention-gated, learned EWMA save-to-build lead/lag distribution, anticipatory cgroup arming ahead of process spawn, atomic claim upon process detection, randomized holdouts ($p = 0.10$), and closed-loop benefit estimation.
   - **Intention-to-Treat Protocol:** In primary hypothesis testing, every build executed under C2 counts as a C2 build regardless of whether it was randomized into the holdout arm. Counterfactual holdouts are an intrinsic component of C2's self-measurement. Secondary reporting provides decomposed reserved-only and holdout-only medians.
4. **C3 (Static Priority Reservation):** Always-on elevated reservation cgroup (`cpu.weight = 500`) with continuously intensified watcher (10 ms polling interval). Simulates traditional static priority boosting without temporal or contention gating.

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

### Future Work (NOT Run in this Evaluation)
- Multi-process parallel builds (`make -j2`) and multi-phase mixed builds (header generation + compilation + linking) are designated for future multi-core scaling evaluations and are explicitly NOT part of the primary Step 11 evaluation.

### Synthetic Contention Load Levels
Background CPU load is generated using `stress-ng` pinned to the identical CPU set as the build workload (`CPUSET=0-1`, 2 logical cores):
- **Idle:** 0 background workers (clean uninhibited execution).
- **Moderate:** 2 CPU stress hogs per assigned CPU core (`stress-ng --cpu 4 --taskset 0-1`). Induces moderate CPU PSI contention ($\sim 25\% - 45\%$).
- **High:** 4 CPU stress hogs per assigned CPU core (`stress-ng --cpu 8 --taskset 0-1`). Induces severe CPU PSI contention ($\sim 60\% - 95\%$).

---

## 3. Block Rotation Protocol & Ramp-Down Rules

To eliminate thermal throttling artifacts, OS page cache drift, and chronological ordering bias:
- **Latin Square / Block Rotation:** Experiments execute in rotating blocks across conditions:
  $$\text{Block Sequence: } [C_0, C_1, C_2, C_3] \to [C_1, C_2, C_3, C_0] \to [C_2, C_3, C_0, C_1] \to [C_3, C_0, C_1, C_2] \dots$$
- **Block Size:** 10 measured builds per condition block.
- **Cycles:** 5 full cycles per condition = **50 measured builds per condition** (10 per block $\times$ 5 blocks).
- **Warm-Ups:** Each condition executes **12 unmeasured warm-up builds** on Cycle 1 prior to starting measured blocks. For C2, warm-ups allow the EWMA predictor and inotify monitor to establish steady-state convergence.
- **Daemon Start Timing:** For conditions C1, C2, C3 and ablations, the daemon process is started BEFORE the PSI settle wait begins, ensuring background monitoring and housekeeping threads are fully initialized before builds commence. C0 executes with no daemon.
- **PSI Ramp-Up Settling Delay:** Following synthetic load escalation (`load.sh moderate` or `load.sh high`), the harness enforces a **12-second settling pause** for `/proc/pressure/cpu` 10-second rolling averages to stabilize.
- **PSI Ramp-Down Rule:** After any decrease in load (e.g. High $\to$ Idle, or Moderate $\to$ Idle), the harness monitors `/proc/pressure/cpu` and blocks until `some_avg10 < 5.0%` (or at most 40.0 s) before starting the next block. Blocks at Idle MUST start with `some_avg10 < 5.0%`. System CPU PSI is logged at the start of every block.
- **Watchdog Timeout:** Each individual build is guarded by a hard 60.0 s watchdog timeout.

---

## 4. Evaluated Metrics

The evaluation harness collects and persists strictly the following metrics across JSONL logs, SQLite databases, and summary tables:
1. **Wall-Clock Build Duration ($T_{\text{wall}}$):** Total elapsed build time in seconds, measured via high-resolution monotonic timestamps ($t_{\text{end}} - t_{\text{start}}$).
2. **Process CPU Time ($T_{\text{cpu}}$):** Cumulative user + system CPU seconds consumed by the build process tree, read via child process resource usage (`ru_utime + ru_stime`).
3. **Cgroup CPU Share ($S_{\text{cpu}}$):** Ratio of reservation group CPU time to elapsed wall time:
   $$S_{\text{cpu}} = \frac{\Delta \text{usage\_usec}}{10^6 \times \Delta t_{\text{wall}}}$$
   For boosted conditions, builds with $S_{\text{cpu}} < 0.50$ are flagged as partially unboosted.
4. **Claim Latency ($L_{\text{claim}}$):** Elapsed duration in milliseconds from the process's initial appearance in `/proc` to cgroup migration completion ($t_{\text{claim\_end}} - t_{\text{seen}}$).
5. **Prediction Hit Rate ($R_{\text{hit}}$):** Percentage of scored predictions where the matching build process arrived within $[W_{\text{lo}}, W_{\text{hi}}]$, queried from the condition database:
   $$R_{\text{hit}} = \frac{N_{\text{hit}}}{N_{\text{hit}} + N_{\text{miss}}}$$
6. **False Reservation Rate ($R_{\text{false}}$):** Percentage of armed reservations that expired unclaimed without a build arriving:
   $$R_{\text{false}} = \frac{N_{\text{expired}}}{N_{\text{total\_armed}}}$$
7. **Missed Prediction Rate ($R_{\text{miss}}$):** Percentage of observed builds spawned without an armed reservation ready:
   $$R_{\text{miss}} = \frac{N_{\text{unpredicted}}}{N_{\text{total\_builds}}}$$
8. **Watcher Scan Frequency and Duration:** Total `/proc` scans, scans per second ($f_{\text{scan}}$), and average scan duration ($\bar{t}_{\text{scan}}$ in ms) across normal and intensified modes, parsed from daemon shutdown statistics.
9. **Daemon CPU Percentage ($P_{\text{daemon}}$):** CPU utilization of the PreCompile daemon process over each block, computed from `/proc/<pid>/stat` ticks:
   $$P_{\text{daemon}} = \frac{\Delta (\text{utime} + \text{stime})}{\text{SC\_CLK\_TCK} \times \Delta t_{\text{wall}}} \times 100\%$$
10. **Background Impact ($I_{\text{bg}}$):** Aggregate CPU seconds per wall-clock second consumed by `stress-ng` background worker threads during the block:
    $$I_{\text{bg}} = \frac{\Delta (\sum \text{hog\_utime} + \sum \text{hog\_stime})}{\Delta t_{\text{wall}}}$$

---

## 5. Statistical Testing & Significance Control

### Primary Null Hypotheses ($H_0$)
For Scenario A at each contested load level (Moderate, High), family-wise error rate is controlled across $m = 3$ tested hypotheses ($m = 6$ combined):
1. **$H_{0,1}$ (Contended Speedup vs Unboosted):** $T_{\text{wall}}(C_2) \ge T_{\text{wall}}(C_0)$ in Scenario A.
2. **$H_{0,2}$ (Anticipatory vs Reactive Duration):** $T_{\text{wall}}(C_2) = T_{\text{wall}}(C_1)$ in Scenario A.
3. **$H_{0,3}$ (Claim Latency Advantage):** $L_{\text{claim}}(C_2) \ge L_{\text{claim}}(C_1)$.

### Hypothesis Testing Protocol
- **Test Statistic:** Two-sided Mann-Whitney U test with exact tie correction across independent blocks.
- **Family-Wise Error Rate (FWER):** Controlled at $\alpha = 0.05$ using the step-down **Holm-Bonferroni correction**:
  $$p_{(i)} \le \frac{\alpha}{m - i + 1}$$
  where $m$ is the total number of tested hypotheses for that family.
- **Confidence Intervals:** 95% non-parametric confidence intervals computed via 1,000 stratified bootstrap iterations.
- **First-Build Sensitivity:** Claim latency and build durations are evaluated both across all builds and excluding the first build of each block (`block_build_index == 0`) to distinguish cold-start daemon artifacts from steady-state operation.

---

## 6. Pre-Registered Success Criteria (S1–S7)

System performance and claims are assessed strictly against the following criteria:

| Criterion | Definition & Metric | Target Requirement | Pre-Declared Assessment & Fallback Rule |
| :--- | :--- | :--- | :--- |
| **S1: Contended Speedup** | $T_{\text{wall}}(C_2)$ vs $T_{\text{wall}}(C_0)$ in Scenario A (Moderate & High load) | Relative median reduction $\ge 20.0\%$ with Holm-corrected $p < 0.05$ | Core speedup benchmark. If failed, system fails primary performance goal. |
| **S2: Anticipatory vs Reactive** | $T_{\text{wall}}(C_2)$ vs $T_{\text{wall}}(C_1)$ in Scenario A (Moderate & High load) | Report effect and 95% bootstrap CI; **No artificial threshold** | **Pre-declared:** Build-time delta is expected to fall within measurement noise (tens of ms on multi-second builds). If $C_2$ does not beat $C_1$ significantly, PreCompile's contribution is assessed on precision (S4), overhead (S5), and bounded harm (S6), as specified in IDF Section 8. |
| **S3: Claim Latency** | $L_{\text{claim}}(C_2)$ vs $L_{\text{claim}}(C_1)$ in Scenario A (High load) | $C_2$ median claim latency at least $25.0\%$ lower than $C_1$ ($p_{\text{Holm}} < 0.05$), tested with and without index 0 | Confirms intensified watcher / anticipatory arming advantage. |
| **S4: Precision on Irregular Workloads** | $R_{\text{false}}$ in Scenario B, Arming Rate in Scenario C | $R_{\text{false}}(C_2) \le 20.0\%$ in Scenario B; Reservations armed $\le 20.0\%$ in Scenario C | **Pre-declared expectation (gate simulation):** This criterion is likely to FAIL under uniform $U(0.5, 20)$ irregular delays (simulation arms 31–40% of saves). If it fails, the technical claim becomes "bounded harm under misprediction, not complete abstention". |
| **S5: Monitoring Overhead** | Daemon CPU % ($P_{\text{daemon}}$) and average scan time ($\bar{t}_{\text{scan}}$) | $P_{\text{daemon}} \le 1.0\%$ of one core; $\bar{t}_{\text{scan}} \le 1.5\text{ ms}$ | Assesses lightweight daemon monitoring invariant across all conditions. |
| **S6: Bounded Harm & Safety** | Idle arming rate, idle build duration delta, background hog throughput | Idle arming rate $\le 10.0\%$ after ramp-down wait; boosted idle $T_{\text{wall}}$ within $\pm 5.0\%$ of $C_0$; hog impact of $C_2$ not worse than $C_3$ | Confirms no regression under clean developer environments. |
| **S7: Resource Cleanup** | Lingering cgroups and orphan processes | Exactly 0 leftover `precompile-res-*` cgroups and 0 orphaned background processes | Safety invariant verified after every block and evaluation conclusion. |

---

## 7. Execution Time Arithmetic

Based on empirical pilot build durations and the block protocol:

### Tier 1 (Scenario A: 4 Conditions $\times$ 3 Loads $\times$ 5 Cycles = 60 Blocks)
- **Measured Builds:** 60 blocks $\times$ 10 builds = 600 builds.
- **Warm-Up Builds:** 12 conditions/loads $\times$ 12 builds on Cycle 1 = 144 builds.
- **Per-Build Durations (Build + 1.8s Delay):**
  - High Load: $C_0 \approx 10.0\text{s}$, $C_1/C_3 \approx 3.8\text{s}$, $C_2 \approx 4.4\text{s}$ (mean $\approx 5.5\text{s}$). Block $\approx 55\text{s}$.
  - Moderate Load: $C_0 \approx 6.6\text{s}$, $C_1/C_2/C_3 \approx 3.9\text{s}$ (mean $\approx 4.5\text{s}$). Block $\approx 45\text{s}$.
  - Idle Load: All conditions $\approx 4.3\text{s}$ (mean $\approx 4.3\text{s}$). Block $\approx 43\text{s}$.
  - Mean block build time across 60 blocks: $\approx 48.0\text{ s}$.
- **Pure Measured Build Time:** $60 \text{ blocks} \times 48.0\text{ s} = 2,880\text{ s}$ (48.0 min).
- **Warm-Up Time:** 144 builds $\times \sim 3.5\text{ s} = 504\text{ s}$ (8.4 min).
- **PSI Ramp-Up Waits:** 2 load transitions $\times 12\text{s} = 24\text{ s}$.
- **PSI Ramp-Down Waits:** 1 load transition $\times 35\text{s} = 35\text{ s}$.
- **Daemon Lifecycle Overhead:** 60 blocks $\times 1.5\text{s} = 90\text{ s}$.
- **Estimated Tier 1 Wall Time:** $2,880 + 504 + 24 + 35 + 90 = \mathbf{3,533\text{ s}}$ (**~58.9 minutes / ~0.98 hours**).

### Tier 2 (Scenario B & Scenario C at Moderate Load)
- **Scenario B (Irregular Delays):** 4 conditions $\times$ 30 builds = 120 builds across 12 blocks.
  - Mean delay: $10.25\text{ s}$, mean build time: $3.0\text{ s} \implies 13.25\text{ s}$ per build.
  - Total Scenario B: $120 \times 13.25\text{ s} = 1,590\text{ s}$ (26.5 min).
- **Scenario C (Save-Only):** 4 conditions $\times$ 50 saves = 200 saves.
  - Interval: $2.0\text{ s}$ per save $\implies 200 \times 2.0\text{s} = 400\text{ s}$ (6.7 min).
- **Estimated Tier 2 Wall Time:** $1,590 + 400 = \mathbf{1,990\text{ s}}$ (**~33.2 minutes / ~0.55 hours**).

### Combined Evaluation Wall Time
$$\text{Total Wall Time} = 3,533\text{ s} + 1,990\text{ s} = \mathbf{5,523\text{ s}} \quad (\mathbf{\sim 92.0\text{ minutes / 1.53 hours}})$$
