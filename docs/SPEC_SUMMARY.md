# PreCompile: System Specification Summary

This document summarizes the design, architecture, equations, state machines, and protocols defined in the Invention Disclosure Document (*IDF-B_PreCompile_Final.pdf*).

---

## 1. System Components & Stage Mapping

PreCompile is an anticipatory, user-space CPU scheduling system designed for Linux (cgroup v2). It predicts process creation from upstream user actions (e.g., file saves prior to software builds) and prepares an empty, elevated-priority cgroup reservation before the process starts.

| Stage | Name | Key Responsibilities | Module |
|---|---|---|---|
| **Stage 1** | **Event Detection** | Watches registered project directories using `inotify` (closing after write, moving into directory). Debounces bursts of saves to the timestamp of the last save. Suppresses saves while a claimed build runs. Filters out build artifacts, temp files, and VCS metadata. | `precompile/monitor.py` |
| **Stage 2** | **Process Creation & Identity Matching** | Observes new processes via `/proc` polling (first pass) or netlink process connector (`cn_proc`). Matches executables against project tool profiles, verifies resolved working directories lie within project roots, and tracks parent-child process hierarchy. | `precompile/watcher.py`, `precompile/matcher.py` |
| **Stage 3** | **Event-Conditioned Latency Learning** | Records delay $x$ between the debounced save and the first matching build process. Discards samples beyond 30 s correlation window. Maintains running EWMA mean and variance per `(project, file_class, tool_profile)`. Applies 3-$\sigma$ outlier clipping. | `precompile/predictor.py`, `precompile/store.py` |
| **Stage 4** | **Confidence Gate & Prediction Window** | Computes prediction window $[mean - k\sigma - m, mean + k\sigma + m]$. Evaluates confidence gate: sample count $\ge 5$, coefficient of variation $CV \le 0.5$ (or $\sigma \le 0.25\text{ s}$ for small means), and rolling hit-rate $\ge 0.6$ over last 10 predictions (once $\ge 5$ exist). Misses lower the hit-rate. | `precompile/predictor.py`, `precompile/gates.py` |
| **Stage 5** | **Contention-Gated, Cost-Aware Arming** | Checks Linux CPU Pressure Stall Information (`/proc/pressure/cpu`). Compares expected benefit against expected cost of a false reservation. Suppresses reservation if CPU is uncontented or expected benefit does not exceed cost. | `precompile/gates.py` |
| **Stage 6** | **Anticipatory Reservation** | Allocates an empty cgroup v2 with elevated `cpu.weight` before the build starts. Initializes reservation record in `ARMED` state. Enforces system-wide and per-project concurrency limits (max 1 active reservation per project). A newer save resets the reservation window. Optionally warms page cache. | `precompile/reservation.py`, `precompile/cgroup.py` |
| **Stage 7** | **Identity-Bound Claiming** | Intensifies process scanning when the window opens (`ACTIVE` state). Performs atomic test-and-set claim on the first matching process (even if it arrives early in `ARMED`). Migrates process and existing children into the cgroup; new descendants inherit automatically. Transitions to `CLAIMED`. | `precompile/reservation.py`, `precompile/matcher.py`, `precompile/cgroup.py` |
| **Stage 8** | **Expiry, Release & Feedback** | Manages full lifecycle (`IDLE`, `ARMED`, `ACTIVE`, `CLAIMED`, `EXPIRED`, `CLOSED`, `CANCELLED`). Transitions to `CLOSED` when cgroup becomes empty or max lifetime expires; transitions to `EXPIRED` if window elapses unclaimed. Cleans cgroups and feeds delays/hit/miss outcomes to learning engine. | `precompile/reservation.py`, `precompile/store.py` |
| **Stage 9** | **Self-Measured Benefit** | Randomly withholds a small percentage of passed reservations as holdouts. Measures completion time of reserved vs. holdout builds under matched CPU contention levels. Adjusts reservation weight downward or disables arming if benefit is negligible. | `precompile/benefit.py` |
| **Stage 10** | **Safety, Bounded Harm & Fallback** | Restricts priority to standard CFS/cgroup v2 `cpu.weight` (never uses real-time scheduling classes like `SCHED_FIFO`/`SCHED_RR`). Enforces weight caps, maximum boost lifetimes, and periodic budget limits. Cleans stale cgroups at boot. Degrades to observation-only mode if unprivileged. | `precompile/safety.py`, `precompile/config.py` |

---

## 2. End-to-End Data Flow

```
[Developer IDE / Editor]
        │
        ▼ (File Save: IN_CLOSE_WRITE / IN_MOVED_TO)
[Stage 1: File Event Monitor] ── (Debounced save event)
        │
        ▼
[Stage 3 & 4: Predictor & Confidence Gate]
   ├── Checks: samples >= 5, CV <= 0.5 (or σ <= 0.25s), hit_rate >= 0.6
   └── Fails gate? ──► [Observe Only / Record No-op]
        │ Passes
        ▼
[Stage 5: Arming Decision (Contention Gate & Cost-Benefit)]
   ├── Reads: /proc/pressure/cpu (PSI)
   ├── Evaluates: Expected Benefit > Expected Cost
   └── Fails or Selected for Holdout? ──► [Holdout / Observe Only]
        │ Passes
        ▼
[Stage 6: Reservation Manager]
   ├── Creates empty cgroup v2 with elevated cpu.weight
   ├── State: ARMED (window timer started)
   └── Window opens ──► State: ACTIVE
        │
        │ [Stage 2: Process Watcher (/proc polling / cn_proc)]
        │    Detects newly spawned candidate process
        ▼
[Stage 7: Identity Matching & Atomic Claim]
   ├── Verifies: executable in tool_profile, cwd in project root, not already in cgroup
   ├── Resolves ancestor if multiple candidates appear simultaneously
   ├── Atomic test-and-set claim flag (ARMED or ACTIVE ──► CLAIMED)
   └── Moves PID + child tree into cgroup
        │
        ▼
[Linux CFS Kernel Scheduler] (Build executes with reserved CPU share)
        │
        ▼ (Build finishes: cgroup.procs becomes empty)
[Stage 8: Cleanup & Feedback]
   ├── State: CLOSED (or EXPIRED if window timed out unclaimed)
   ├── cgroup removed, priority reverted
   ├── Latency sample, hit/miss outcome, build duration stored
   └── Stage 9 updates self-measured benefit estimator
```

---

## 3. Predictor Equations and Exact Thresholds

### 3.1 Latency Learning (EWMA with Outlier Clipping)
For an observed delay sample $x_n$ between the debounced save and process start:

1. **Correlation Window Filtering:**
   $$x_n \le 30\text{ s}$$
   Delays $> 30\text{ s}$ are discarded as unrelated to the save event.

2. **3-$\sigma$ Outlier Clipping (once sufficient samples exist):**
   $$x_n^{\text{clipped}} = \max\left(\mu_{n-1} - 3\sigma_{n-1},\, \min(x_n,\, \mu_{n-1} + 3\sigma_{n-1})\right)$$

3. **Exponentially Weighted Moving Average (EWMA) with smoothing factor $\alpha = 0.2$:**
   $$\mu_n = (1 - \alpha)\mu_{n-1} + \alpha \, x_n^{\text{clipped}}$$
   $$\sigma_n^2 = (1 - \alpha)\left(\sigma_{n-1}^2 + \alpha (x_n^{\text{clipped}} - \mu_{n-1})^2\right)$$
   $$\sigma_n = \sqrt{\sigma_n^2}$$
   *(With $\alpha = 0.2$, the estimator adapts within approximately 5 to 10 builds).*

### 3.2 Prediction Window Calculation
The reservation window is defined as:
$$W = \left[\max(0,\, \mu_n - k\sigma_n - m),\; \mu_n + k\sigma_n + m\right]$$
Where:
- $k = 2$ (coverage multiplier)
- $m = 0.3\text{ s}$ (safety margin)

### 3.3 Confidence Gate Conditions
A prediction is generated and considered confident **if and only if** all of the following hold:
1. **Sample Count:**
   $$N \ge 5$$
2. **Variability Threshold:**
   $$CV = \frac{\sigma_n}{\mu_n} \le 0.5 \quad\text{OR}\quad \sigma_n \le 0.25\text{ s} \quad\text{(for very small means)}$$
3. **Recent Hit-Rate:**
   $$\text{Hit-Rate}_{\text{last 10}} \ge 0.6 \quad\text{(enforced once at least 5 predictions have been made)}$$

---

## 4. Reservation State Machine

```
                 ┌────────────────────────────────────────────────┐
                 │                 early match:                   │
                 │      process appears before window opens       │
                 ▼                                                │
   save + gate   ┌───────────┐    window opens    ┌──────────┐    │
──► passed ─────►│   ARMED   ├───────────────────►│  ACTIVE  │    │
                 └─────┬─────┘                    └────┬─────┘    │
                       │                               │          │
                       │ window ends,                  │ process  │
                       │ no match                      │ matched  │
                       ▼                               ▼          │
                 ┌───────────┐                    ┌──────────┐    │
                 │  EXPIRED  │                    │ CLAIMED  ├────┘
                 └─────┬─────┘                    └────┬─────┘
                       │                               │ build ends
                       │                               ▼ (cgroup empty / max lifetime)
                       │                          ┌──────────┐
                       │                          │  CLOSED  │
                       │                          └────┬─────┘
                       │                               │
                       ▼                               ▼
               ┌──────────────────────────────────────────────┐
               │         Outcome Recorded / Model Updated     │
               │             (cgroup removed / IDLE)          │
               └──────────────────────────────────────────────┘
                       ▲
                       │ From any state on shutdown, fatal error,
                       │ or project disabled:
                 ┌─────┴─────┐
                 │ CANCELLED │
                 └───────────┘
```

### State Definitions & Transitions:
- **`IDLE`**: No active reservation for this project.
- **`ARMED`**: Reservation record created; empty cgroup configured with elevated weight; waiting for window start. *A newer save while in ARMED or ACTIVE resets the timer instead of spawning duplicate reservations.*
- **`ACTIVE`**: Current time has entered the reservation window $[t_{\text{save}} + \mu - k\sigma - m,\, t_{\text{save}} + \mu + k\sigma + m]$. Process detection polling is intensified.
- **`CLAIMED`**: A matching process has been atomically bound to the reservation. The process and any existing descendants are written into `cgroup.procs`.
  - **Early Claim Path:** If a matching process arrives while still in **`ARMED`** (prior to the calculated window opening), it is **immediately claimed** (`ARMED -> CLAIMED`), and its delay is logged as a valid sample.
- **`EXPIRED`**: Window closed without any matching process appearing. Reservation released; recorded as a miss (lowering the hit-rate).
- **`CLOSED`**: The claimed build process completed (cgroup became empty) or maximum boost lifetime was reached. Resources released and outcome stored.
- **`CANCELLED`**: Transitioned from any state upon daemon shutdown, project unregistration, or fatal error.

---

## 5. Process Claiming Rules

1. **Identity Criteria:**
   - Process executable path matches one of the project's configured `tool_profiles` (e.g., `gcc`, `g++`, `clang`, `make`, `cargo`, `cmake`).
   - Process working directory (with symbolic links resolved via `realpath`) resides within the registered project root.
   - The process is not already inside an existing PreCompile cgroup.
2. **Atomic Test-and-Set:**
   - Claim is executed via an atomic test-and-set flag on the reservation record to ensure exactly one process claims the reservation.
3. **Ancestor Resolution:**
   - If multiple matching candidate processes appear concurrently, the topmost ancestor within the project is claimed.
   - Any child processes already spawned are discovered via the parent-process table and migrated into the cgroup.
   - Descendants created subsequently inherit the cgroup automatically via kernel cgroup v2 inheritance.
4. **Rejection:**
   - Processes matching the tool executable but residing outside the project path are ignored and do not affect reservations or latency models.

---

## 6. Safety Rules & Fallbacks

1. **Fair Scheduling Only:** Real-time scheduling classes (`SCHED_FIFO`, `SCHED_RR`, `SCHED_DEADLINE`) are **strictly prohibited**. Only standard CFS/cgroup v2 `cpu.weight` adjustments are made.
2. **Weight Caps:** Reservation weight is capped (e.g., `cpu.weight <= 500`, default CFS weight is 100).
3. **Maximum Boost Lifetime:** Every reservation has an upper bound on active duration (derived from learned build duration with safety margins) to prevent indefinite priority elevation on stalled or hung builds.
4. **Resource Budgeting:** A leaky-bucket or rolling-window quota limits cumulative elevated CPU time and weight per evaluation interval across the host.
5. **Startup Hygeine:** Stale PreCompile cgroups left by previous crashes or ungraceful reboots are cleaned during daemon initialization.
6. **Graceful Privilege Degradation:** If run without root privileges or without write access to `/sys/fs/cgroup`, the system logs a warning and automatically degrades to **observation-only mode** (recording filesystem saves, process creations, and learning latency without making cgroup adjustments).

---

## 7. Experimental Validation Protocol

### 7.1 Evaluated Conditions
- **C0 (Baseline):** Default Linux scheduler, no PreCompile running.
- **C1 (Reactive Boost):** Boost applied reactively only *after* the build process is detected, with no anticipatory window.
- **C2 (Full PreCompile):** Proposed anticipatory, confidence-gated, contention-aware system.
- **C3 (Static Always-On):** Permanent elevated priority for any build tool process.

### 7.2 Five Ablation Studies
1. **Ablation 1 (Reactive):** Reactive boost applied after process creation.
2. **Ablation 2 (No Confidence Gate):** PreCompile with confidence gate disabled (arms regardless of sample count or variance).
3. **Ablation 3 (No Contention Gate):** PreCompile with PSI contention check disabled (arms even under idle system conditions).
4. **Ablation 4 (Fixed Window):** Fixed static window size instead of learned $[\mu - k\sigma - m,\, \mu + k\sigma + m]$.
5. **Ablation 5 (No Holdouts):** Full system without randomized holdout feedback / self-measurement loop.

### 7.3 Workload & Trigger Scenarios
- **Scenario A (Automatic Build):** Incremental single-file rebuild triggered by build-on-save script after fixed/stable delay.
- **Scenario B (Irregular Manual Build):** Build launched manually at unpredictable intervals (gate should abstain).
- **Scenario C (Save Without Build):** Saves not followed by builds (gate should prevent or cleanly expire without degradation).
- **Contention Levels:** Background CPU load generated via `stress-ng` at low, moderate, and high utilization.

### 7.4 Quantitative Metrics & Statistical Criteria
- **Metrics:**
  - Build completion duration (median and 95th percentile).
  - Prediction hit rate ($\ge 0.6$).
  - False reservation rate and missed prediction rate.
  - Delay from process creation to cgroup claim.
  - Monitoring CPU/memory overhead.
  - Impact on concurrent background tasks.
- **Protocol:**
  - At least 50 repeated, interleaved runs per condition.
  - Statistical significance evaluated using non-parametric tests (Mann-Whitney U / Wilcoxon) with 95% confidence intervals.
- **Mechanism Verification Tests:**
  - *Concurrency Test:* Inject multiple matching processes concurrently; verify atomic test-and-set assigns exactly one claim.
  - *Holdout Accuracy Test:* Verify that the self-measured benefit from withheld runs statistically matches the controlled C2 vs. C0 benefit.
