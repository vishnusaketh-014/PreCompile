# PreCompile Evaluation: Pre-Registered Experiment Plan

**Document Version:** 1.0.0  
**Registration Timestamp:** 2026-10-20T09:16:47Z  
**Stage Mapping:** Step 11a / Step 11b (System Design Specification Stage 11)  
**Execution Environment:** WSL2 Linux 6.18.33.2-microsoft-standard-WSL2 on 13th Gen Intel(R) Core(TM) i7-1355U (12 vCPUs, 7.6 GiB RAM)

---

## 1. Experimental Conditions

The evaluation suite investigates the full operational spectrum of PreCompile alongside baseline alternatives and targeted ablations:

### Primary Baseline Conditions
1. **C0 (Unboosted Baseline):** Observation-only daemon running. Build processes execute in the default user cgroup (`/sys/fs/cgroup/init.scope` with `cpu.weight = 100`). Measures natural contention slowdown without scheduling intervention.
2. **C1 (Reactive Boost):** Process-spawn reactive boosting. No anticipatory reservation on save. When a matching build process is discovered in `/proc`, if system CPU PSI $\ge 10.0\%$, PreCompile creates a reservation cgroup (`cpu.weight = 500`) and migrates the process.
3. **C2 (Full Anticipatory Pre-Reservation):** Full PreCompile system. Confidence-gated, contention-gated, learned EWMA save-to-build lead/lag distribution, anticipatory cgroup arming ahead of process spawn, atomic claim upon process detection, randomized holdouts ($p = 0.10$), and closed-loop benefit estimation.
4. **C3 (Static Priority Reservation):** Always-on elevated reservation cgroup (`cpu.weight = 500`) with continuously intensified watcher (10 ms polling interval). Simulates traditional static priority boosting without temporal or contention gating.

### Ablation Conditions (System Components)
5. **A1 (Ablation: Reactive):** Functional alias for C1, evaluated directly within the ablation matrix.
6. **A2 (Ablation: No Confidence Gate):** Always arms on file save regardless of whether the predictor has accumulated minimum sample size ($n < 5$) or met accuracy thresholds.
7. **A3 (Ablation: No Contention Gate):** Arms on file save whenever the confidence gate passes, ignoring system CPU PSI (`/proc/pressure/cpu`).
8. **A4 (Ablation: Fixed Window):** Arms using a fixed lead/lag window ($[0.0\text{s}, 5.0\text{s}]$) rather than the learned EWMA dispersion distribution $[\hat{\mu} - 2\hat{\sigma}, \hat{\mu} + 2\hat{\sigma}]$.
9. **A5 (Ablation: No Holdout):** Sets holdout probability to 0.0 ($100\%$ reservation rate), disabling counterfactual holdouts and benefit controller feedback.

---

## 2. Workload Scenarios & Load Levels

### Workloads
- **Scenario A (CPU-Bound Single Translation Unit):** Single-threaded GCC compilation of a heavy C source file (`big.c` with 25,000 synthetic AST nodes / statements). Stresses CPU execution time and compiler backend passes (`cc1`).
- **Scenario B (Multi-Process Build):** Multi-process project build using `make -j2` compiling multiple compilation units in parallel. Stresses process hierarchy discovery, fixpoint descendant sweeping, and multi-PID cgroup migration.
- **Scenario C (Multi-Phase Mixed Build):** Multi-step workflow involving header generation, preprocessing, compilation, and linking.

### Synthetic Contention Load Levels
Background CPU load is generated using `stress-ng` pinned to the identical CPU set as the build workload (`CPUSET=0-1`, 2 logical cores):
- **Idle:** 0 background workers (clean uninhibited execution).
- **Moderate:** 2 CPU stress hogs per assigned CPU core (`stress-ng --cpu 4 --taskset 0-1`). Induces moderate CPU PSI contention ($\sim 25\% - 45\%$).
- **High:** 4 CPU stress hogs per assigned CPU core (`stress-ng --cpu 8 --taskset 0-1`). Induces severe CPU PSI contention ($\sim 60\% - 95\%$).

---

## 3. Block Rotation Protocol & Warm-Up Rules

To eliminate thermal throttling artifacts, background OS cache drift, and chronological ordering bias:
- **Latin Square / Block Rotation:** Experiments execute in rotating blocks across conditions:
  $$\text{Block Sequence: } [C_0, C_1, C_2, C_3] \to [C_1, C_2, C_3, C_0] \to [C_2, C_3, C_0, C_1] \to [C_3, C_0, C_1, C_2] \dots$$
- **Block Size:** 10 measured builds per condition block.
- **Cycles:** 5 full cycles per condition = **50 measured builds per condition**.
- **Warm-Ups:** Each condition executes **12 unmeasured warm-up builds** prior to starting measured blocks. For C2, warm-ups allow the EWMA predictor and inotify monitor to establish steady-state convergence.
- **PSI Settling Delay:** Following synthetic load transitions (`load.sh high`), the harness enforces a **12-second settling pause** for `/proc/pressure/cpu` 10-second rolling averages to stabilize before issuing builds.
- **Watchdog Timeout:** Each individual build is guarded by a hard 3x expected duration timeout (maximum 120s) to abort hung runs cleanly.

---

## 4. Evaluated Metrics

For every build, the evaluation harness collects and persists the following metrics to append-only JSONL logs and SQLite:
1. **Wall-Clock Duration ($T_{\text{wall}}$):** Total elapsed build time in seconds, measured via `pidfd_open` exit notification.
2. **Process CPU Time ($T_{\text{cpu}}$):** Cumulative user + system CPU time consumed by the build process tree, read from `/proc/<pid>/stat`.
3. **Cgroup CPU Share ($S_{\text{cpu}}$):** Ratio of reservation group CPU time to total elapsed wall time:
   $$S_{\text{cpu}} = \frac{\Delta \text{usage\_usec}}{10^6 \times \Delta t_{\text{wall}}}$$
4. **Prediction Hit Rate ($R_{\text{hit}}$):** Percentage of armed predictions where the matching build process arrived within $[W_{\text{lo}}, W_{\text{hi}}]$.
5. **False Reservation Rate ($R_{\text{false}}$):** Percentage of armed reservations that expired unclaimed without a build arriving.
6. **Missed Prediction Rate ($R_{\text{miss}}$):** Percentage of build processes spawned without an armed reservation ready.
7. **Claim Latency ($L_{\text{claim}}$):** Elapsed duration in milliseconds from the process's initial appearance in `/proc` to cgroup migration completion.
8. **Daemon Overhead:** Average `/proc` scan duration (ms), scans/second across normal and intensified modes, and total daemon CPU percentage.
9. **Background Impact ($I_{\text{bg}}$):** Relative throughput degradation of background `stress-ng` compute workers during build execution.

---

## 5. Statistical Testing & Significance Control

### Primary Null Hypotheses ($H_0$)
1. **$H_{0,1}$ (Build Speedup under High Contention):** $T_{\text{wall}}(C_2) = T_{\text{wall}}(C_0)$ in Scenario A/B under high load.
2. **$H_{0,2}$ (Anticipatory vs Reactive):** $T_{\text{wall}}(C_2) = T_{\text{wall}}(C_1)$ in Scenario A/B under high load.
3. **$H_{0,3}$ (Claim Latency Advantage):** $L_{\text{claim}}(C_2) = L_{\text{claim}}(C_1)$ under high load.
4. **$H_{0,4}$ (Daemon Overhead vs Static):** $\text{Scans}_{\text{total}}(C_2) = \text{Scans}_{\text{total}}(C_3)$.
5. **$H_{0,5}$ (Background Worker Impact):** $I_{\text{bg}}(C_2) = I_{\text{bg}}(C_3)$ under moderate load.

### Hypothesis Testing Protocol
- **Test Statistic:** Two-sided Mann-Whitney U test with exact tie correction across independent blocks.
- **Family-Wise Error Rate (FWER):** Controlled at $\alpha = 0.05$ using the step-down **Holm-Bonferroni correction**:
  $$p_{(i)} \le \frac{\alpha}{m - i + 1}$$
  where $m$ is the total number of tested hypotheses.
- **Confidence Intervals:** 95% non-parametric confidence intervals computed via 1,000 stratified bootstrap iterations.

---

## 6. Mechanism Tests

1. **Double-Claim Concurrency Test:**
   - Evaluates the atomic claim guard (`atomic_claim=True` vs simulated unprotected race).
   - Simulates simultaneous candidate process spawns to prove that exactly one process can claim a reservation, while subsequent attempts are safely rejected.
2. **Holdout Equivalence Test:**
   - Within condition C2 (with holdout probability $p = 0.50$), compares `reserved` builds against randomly selected `holdout` builds under identical system contention.
   - Proves that observed speedup differences arise directly from cgroup priority elevation rather than unobserved environmental covariates.

---

## 7. Fixed Pre-Registered Success Criteria

To eliminate post-hoc confirmation bias, system success is determined strictly against the following pre-registered criteria:

| Criterion | Target Requirement | Evaluation Condition |
| :--- | :--- | :--- |
| **C-1: Contended Speedup** | $\ge 15.0\%$ reduction in median $T_{\text{wall}}$ vs $C_0$ ($p_{\text{Holm}} < 0.05$) | Scenario A & B, High Load |
| **C-2: Anticipatory Advantage**| $\ge 5.0\%$ reduction in median $T_{\text{wall}}$ vs $C_1$ ($p_{\text{Holm}} < 0.05$) | Scenario A & B, High Load |
| **C-3: Claim Latency** | Median claim latency $\le 25.0\text{ ms}$ ($\ge 35\%$ lower than $C_1$) | High Load |
| **C-4: Daemon Overhead** | Daemon CPU $\le 1.0\%$; average scan duration $\le 1.5\text{ ms}$ | All Conditions |
| **C-5: Bounded Harm / Safety** | Idle arming rate $\le 10\%$; background degradation $\ge 40\%$ lower than $C_3$ | Idle & Moderate Load |
| **C-6: Resource Cleanup** | Exactly 0 leftover cgroups and 0 orphaned background processes | All Runs |

---

## 8. Machine Environment Specification

- **Host OS:** Windows 11 x86_64
- **WSL Kernel:** Linux 6.18.33.2-microsoft-standard-WSL2 (x86_64)
- **Processor:** 13th Gen Intel(R) Core(TM) i7-1355U (12 virtual cores, base frequency 1.70 GHz)
- **Memory:** 7.6 GiB total system RAM, 2.0 GiB Swap
- **Control Group Hierarchy:** Linux cgroup v2 unified hierarchy mounted at `/sys/fs/cgroup` with `cpu` controller enabled.
- **Storage Subsystem:** Ext4 root filesystem for test repositories (`/home/saketh/precompile-demo-heavy`).
