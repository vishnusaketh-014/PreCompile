# PreCompile Mission Control Dashboard

A high-fidelity, demo-ready web monitoring and visualization interface for **PreCompile: Anticipatory Priority Reservation for Software Builds**.

---

## 1. Architecture & Design Principles

The PreCompile dashboard operates under strict architectural isolation:

1. **Read-Only SQLite Guarantee (`mode=ro`):**
   - Opened strictly using SQLite URI mode `file:<db_path>?mode=ro`.
   - The dashboard process has **zero write permissions** and cannot execute schema migrations, updates, or deletions. Any write attempt fails immediately with `sqlite3.OperationalError: attempt to write a readonly database`.
2. **Actuation Isolation:**
   - The dashboard is completely decoupled from the Linux `cgroup v2` tree and scheduler controls. It does not send signals or IPC commands to the daemon.
3. **Minimal Host Scheduling Disturbance:**
   - PreCompile's core research value centers on sub-second build scheduling latencies. The dashboard server streams updates at **1.0 Hz** via Server-Sent Events (SSE) using Python's standard library `http.server`, introducing negligible CPU overhead (<0.05% CPU).
4. **Zero Build Step, Zero CDN Dependencies:**
   - Pure vanilla ES6, semantic CSS variables, and self-contained vector SVG chart generators.
   - Works 100% offline inside WSL2 and connects to the Windows host browser via `http://localhost:8787`.
5. **Absolute Data Honesty & Provenance:**
   - Every metric on screen derives directly from SQLite tables or recorded benchmark JSONL files.
   - Empty states explicitly report `"no data yet"` with zero invented numbers.
   - Every view indicates its data source origin (e.g. `live_sqlite: precompile.db` or `replay: results_tier1`).

---

## 2. Quickstart & Launch Commands

### Launching the Dashboard

```bash
# Launch on default port 8787
precompile dashboard

# Or specify custom port and database path
precompile dashboard --port 8787 --db /var/lib/precompile/precompile.db

# Or run via Python module syntax
python3 -m precompile dashboard --port 8787
```

### Accessing the Web UI

- **WSL2 / Linux:** Open your Windows browser and navigate to:  
  **`http://localhost:8787`** or **`http://127.0.0.1:8787`**
- **Keyboard Shortcuts:**
  - `P`: Toggle **Presentation Mode** (enlarges typography $+30\%$, auto-collapses left navigation for projector display).
  - `1` – `7`: Fast-switch between views (1=Overview, 2=Timeline, 3=Distributions, 4=Benefit, 5=Safety, 6=Experiments, 7=Decisions).
  - `Space`: Play/Pause playback in **Replay Mode**.

---

## 3. View Architecture & Screenshots

### Home Screen: Compare (WITH vs WITHOUT PreCompile) - The Hero Feature
![Compare Screen](screenshots/00_compare.svg)

The **Compare** screen provides an immediate, 5-second answer to: *which setup is faster, by how much, and how confident are we?*

- **Strict Definitions:**
  - **WITHOUT PreCompile:** C0 baseline (no daemon acting; normal CFS scheduling) and counterfactual holdouts in the DB. Visualized in **cool grey-blue (`#90A4AE`)**.
  - **WITH PreCompile:** C2 full predictive mode (priority reservation allocated in cgroup v2 before compiler spawn). Visualized in **electric cyan (`#00E5FF`)**.
  - **Reactive Only (Optional Third Column):** C1 reactive mode (priority elevated after compiler spawn is detected by polling). Visualized in **amber (`#FFB300`)** to keep the comparison honest about what prediction adds over reactive priority.
- **Hero Speedup & Significance Badge:**
  - Displays relative speedup: `(C0 median - C2 median) / C0 median * 100%`.
  - Shows 95% bootstrap confidence interval and non-parametric Mann-Whitney U test p-value.
  - **Honesty Rule:** If a condition is not statistically significant (e.g. Scenario A Idle: $+0.8\%$ delta, $p=0.1347$), the badge prominently says **"No Significant Difference"** instead of showing a green win. In Scenario B irregular delays, it notes where predictor confidence drops and the arming gate abstains.
- **Symmetrical Side-by-Side Panels:**
  - Measured build time in large tabular mono numerals (e.g. `8.38s` vs `2.04s`).
  - CFS CPU share and claim latency ($+47\text{ms}$ reactive scan lag vs preemptive negative lead time applied *before* spawn).
  - **Synchronized Animated Progress Bars:** Replaying the build lets viewers see WITH PreCompile complete noticeably sooner across the room.
- **Overlaid CPU-Share Timeline:**
  - Both runs plotted on the exact same time axis with vertical event markers for **Save Event** ($t=0$), **Reservation Applied**, **Actual Spawn**, and **Completion**.
- **Live A/B Demo Mode:**
  - Click **"Run Live A/B Demo"** to launch balanced pairs alternating order (`WITHOUT -> WITH`, `WITH -> WITHOUT`) to eliminate warm-up and thermal drift.
  - If Linux cgroup v2 or root privileges are unavailable on the current host, an honest fallback banner automatically appears and switches to recorded empirical playback.

---

### View 1: Live Overview (Mission Control Telemetry)
![Live Overview Screen](screenshots/01_overview.svg)

- **Anticipatory State-Machine Strip:** Visualizes the full lifecycle:  
  `[Idle] -> [Observing] -> [Predicted] -> [Armed] -> [Reserved] -> [Released]`  
  The active state glows with an electric cyan pulse (`#00E5FF`), displaying current state status and reason.
- **5 Hero KPI Tiles:**
  - **Prediction Confidence:** Displays rolling confidence score (e.g. `92.4%`, `CV: 0.11`, `n=50`, gate: `PASS`).
  - **Save-To-Build Delay:** Target save-to-process spawn latency $\mu \pm \sigma$ with window interval `[1.32s, 2.32s]`.
  - **Cgroup Weight:** Elevated CFS weight (e.g. `500` for a $5:1$ competitive CPU share vs `100` baseline).
  - **System CPU PSI:** Real-time host pressure (`/proc/pressure/cpu`) with threshold tags (`Idle`, `Moderate`, `High Contention`).
  - **Safety & Governor:** Status of kill-switch, circuit-breaker, and rolling budget gauge.
- **Transition Log:** Real-time chronological audit trail of state transitions with cgroup names, PIDs, and migration latencies.

---

### View 2: Prediction Timeline
![Prediction Timeline Screen](screenshots/02_timeline.svg)

- **Build Cycle Swimlanes:** Each build cycle displays:
  - 💾 **File Save ($t=0$ anchor):** The filesystem inotify save event.
  - 🎯 **Predicted Window $[LO, HI]$:** Shaded range within which process spawn was anticipated.
  - 🚀 **Actual Process Spawn:** Observed timestamp of matching compiler execution.
  - ⚡ **Atomic Claim:** Measured migration time into elevated cgroup.
  - **Error Latency Bar:** Explicit signed error $\Delta t = t_{\text{actual}} - t_{\text{pred}}$, color-coded green if within window and amber if outside.

---

### View 3: Reserved vs Holdout
![Reserved vs Holdout Screen](screenshots/03_distributions.svg)

- **Empirical Build Duration Distributions:** Side-by-side box & whisker plots (medians, quartiles, and min/max whiskers) overlaid with individual jittered trial points.
- **Statistical Significance Banner:** Displays median delta, relative speedup percentage ($+56.8\%$), 95% bootstrap confidence interval, and non-parametric Mann-Whitney U test p-value ($p < 0.0001$).
- **Contention Bins Breakdown:** Comparative performance cards across **Idle** ($<5\%$ PSI), **Moderate** ($5\text{--}20\%$ PSI), and **High** ($\ge 20\%$ PSI) tranches.

---

### View 4: Benefit Estimator
![Benefit Estimator Screen](screenshots/04_benefit.svg)

- **Closed-Loop Feedback Controller:** Demonstrates the adaptive weight controller adjusting CFS priority based on real counterfactual holdout benefit.
- **Weight Trajectory Chart:** Visual step-chart illustrating weight scaling ($100 \to 250 \to 350 \to 500$).
- **Per-Bin Effect Tranches:** Effect sizes, confidence bounds, and sample counts for independent contention bins.
- **Evaluation History Audit:** Full audit table of all historical controller evaluations and parameter changes.

---

### View 5: Safety Governor
![Safety Governor Screen](screenshots/05_safety.svg)

- **Rolling Boost Budget Gauge:** Visual progress meter tracking consumption against the 900-second (15-minute) budget ceiling in rolling 1-hour windows.
- **Circuit Breaker Status:** Guards against repeated prediction misses (threshold: 3 consecutive misses).
- **Kill-Switch State:** Monitors presence of `/tmp/precompile.pause` for immediate operator pause.
- **Descendant Leak Counter:** Tracks process fixpoint sweep effectiveness (0 leftover processes outside cgroup).
- **Daemon CPU Overhead:** Real-time monitoring showing minimal background load ($\sim 3.4\%$ mean CPU).

---

### View 6: Experimental Evaluation
![Experimental Evaluation Screen](screenshots/06_experiments.svg)

- **Pre-Registered Criteria (S1 - S7):** Complete, honest disclosure of pre-registered outcomes from `RESULTS.md`:
  - **S1 (Contended Speedup):** `PASS` ($+75.6\%$ ITT, $p < 10^{-15}$).
  - **S2 (Anticipatory vs Reactive):** `REPORTED` (C2 matches C1 in median ITT).
  - **S3 (Claim Latency Advantage):** `FAIL` ($+9.4\%$ advantage, failed sub-15ms target due to `/proc` polling resolution).
  - **S4 (Irregular Workload Precision):** `INVALID AS RUN` (documented audit revealing constant 12.97s delay in original Tier 2 harness).
  - **S5 (Monitoring Overhead):** `FAIL` (Daemon CPU averaged $3.45\%$, exceeding $1.0\%$ ceiling during armed polling).
  - **S6 (Bounded Harm):** `PASS` ($+0.82\%$ idle delta, well within $\pm 5\%$).
  - **S7 (Resource Cleanup):** `PASS` (0 orphan cgroups or processes).
- **Condition Matrix (Table 2a):** C0, C1, C2, and C3 across scenarios A, B, and C under all load levels.
- **Mechanism Ablations:** Summary of A1 through A5 ablation runs.

---

### View 7: Decision Log
![Decision Log Screen](screenshots/07_decisions.svg)

- **Real-Time Decision Stream:** Scrollable, filterable audit log of daemon arming and abstention decisions.
- **Filter Categories:** `All`, `Armed`, `Holdout`, and `Abstained` (`abstain_no_contention`, `abstain_confidence`, `abstain_cost`).
- **Cost/Benefit Audit:** Shows exact PSI contention, estimated speedup benefit, and impact cost at decision time.

---

## 4. Demo Replay Engine

The dashboard features a built-in **Replay Engine** to deliver seamless demonstrations even under flaky WSL host load:
1. Click **`[▶ Replay Mode]`** in the top bar.
2. Select any recorded benchmark suite:
   - `Tier 1: Scenario A (Idle / Moderate / High)`
   - `Tier 2: Scenario B (Heavy C/C++)`
   - `Tier 3: Ablations (A2-A5)`
3. Use playback controls:
   - **Play / Pause (`Space`):** Starts automated playback.
   - **Speed Multiplier:** Select from `0.5x`, `1.0x`, `2.0x`, `5.0x`, or `10.0x`.
   - **Step Buttons (`⏮`, `⏭`):** Step forward or backward through individual build trials.
4. When active, a prominent **`● REPLAY`** badge is visible at all times, preserving data honesty.
