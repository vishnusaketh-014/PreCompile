# PreCompile

Anticipatory, confidence-gated scheduling priority reservation for software builds using learned event-to-process latency. Based on the system design specification *IDF-B_PreCompile_Final.pdf*.

---

## Overview

Software builds frequently sit on a developer's critical path and are preceded by file saves. Conventional operating system schedulers only assign priority after a process has started executing, leaving the initial startup interval unprotected under CPU contention.

**PreCompile** learns the latency between file saves and build process spawns. When confidence and contention gates pass, it creates an empty control group (`cgroup v2`) with elevated `cpu.weight` ahead of time. When the matching build process appears, it is atomically claimed and moved into the reservation, eliminating start-up delay without kernel modifications.

---

## Design Decisions

- **Single CLI entry point:** A unified `precompile` binary/script provides all interactions via subcommands (`daemon`, `status`, `projects`, `predict`).
- **/proc polling in the first pass:** Initial process detection polls `/proc` table entries, with the Linux netlink process connector (`cn_proc`) planned as a later zero-latency upgrade.
- **SQLite for dynamic data, YAML for settings:** SQLite (`precompile.db`) manages registered projects, learned latency samples, prediction histories, and holdout records. File-based `config.yaml` is reserved strictly for static daemon configuration and safety thresholds.
- **Root execution with graceful degradation:** The daemon is designed to run as `root` to manipulate cgroup v2 priority (`cpu.weight`). If executed without privilege or write access to `/sys/fs/cgroup`, it degrades gracefully to **observation-only mode** (learning delays and tracking outcomes without altering scheduling), fulfilling Stage 10 of the spec.
- **Two-phase implementation:** The initial implementation pass (Steps 1 through 6) operates strictly in observation mode, collecting events, learning distributions, and validating gates before applying active kernel scheduling modifications (Steps 7 through 10).
- **Outlier clipping sigma floor (`clip_sigma_floor_s = 0.1`):** In Stage 3, delay samples are clipped to $3\sigma$ around the mean once $n \ge 5$. If a developer has very consistent save-to-build latency, $\sigma$ approaches 0. Without a minimum dispersion floor, the clipping interval $[\mu - 3\sigma, \mu + 3\sigma]$ collapses to $[\mu, \mu]$, permanently locking the model and preventing it from adapting to legitimate step changes in workflow (e.g. tool upgrades). Enforcing $s = \max(\sigma, \text{clip\_sigma\_floor\_s})$ ensures the model retains bounded plasticity.
- **Observation-only warm-up interpretation (`strict_min_predictions_to_arm`):** The specification requires rolling hit-rate $\ge 0.6$ over the last 10 predictions "once $\ge 5$ predictions exist". In strict mode (`strict_min_predictions_to_arm=True`, default), `can_arm` remains `False` until at least 5 scored shadow predictions have been collected, enforcing a true observation-only learning period before priority reservations can be activated. In the looser literal reading (`strict_min_predictions_to_arm=False`), the hit-rate rule is bypassed while fewer than 5 predictions exist, permitting early arming if sample count and variance gates pass.
- **Ordering robustness via 2-second spawn buffer:** When builds execute rapidly or within inotify debounce windows ($0.15\text{ s}$), process creation events may be polled slightly before or simultaneously with file close events. A 2.0s sliding spawn buffer pairs late-arriving save events with their originating build process using monotonic start timestamps ($t_{\text{start}} \ge t_{\text{save}} - \text{tolerance}$).
- **Dynamic project reload on SIGHUP:** When running in the background, the daemon handles `SIGHUP` by refreshing its registered project list from SQLite without process restart. It dynamically attaches inotify watches for newly added or re-enabled projects and detaches watches for removed or disabled projects. Any CLI command modifying project state (`projects add/remove/enable/disable`) automatically signals the active daemon via `SIGHUP` if its PID file is found.
- **Contention-gated arming & explicit cost/benefit priors (Step 7):** Arming priority reservations requires both confidence and system contention.
  - *Contention gate:* Evaluates CPU PSI (`/proc/pressure/cpu`), computing $\text{contention\_level} = \max(\text{some avg10}, \text{recent\_some\_pct})$. Contention is present if $\ge 10.0\%$ (configurable threshold), or predicted (treated as present) if $\text{some avg10} \in [5.0\%, 10.0\%)$ and rising ($\text{avg10} > \text{avg60}$).
  - *Cost/benefit gate:* Compares expected saved build time against empty reservation and system impact overhead using explicit priors:
    $$\text{speedup\_frac} = \text{benefit\_prior\_max} \times \min(1, \text{contention\_level} / 50) \quad (\text{prior max} = 0.5)$$
    $$\text{benefit\_s} = p_{\text{hit}} \times \text{build\_dur} \times \text{speedup\_frac}$$
    $$\text{cost\_s} = (1 - p_{\text{hit}}) \times \text{window\_width} \times \text{idle\_cost\_rate} + p_{\text{hit}} \times \text{build\_dur} \times \text{other\_impact\_rate}$$
    with priors $\text{idle\_cost\_rate} = 0.05$ and $\text{other\_impact\_rate} = 0.10$. Step 9 replaces the speedup prior with self-measured empirical benefit once sufficient holdout events accumulate.
  - *Holdout selector:* Randomly withholds a configurable share of qualifying saves (default $10\%$, $\text{holdout\_probability} = 0.1$) as unboosted holdouts for counterfactual evaluation.
  - *Observation-only:* In Step 7, all decisions (`would_arm`, `holdout`, `abstain_*`) are logged and persisted, but no cgroups are created or manipulated.


---

## CLI Usage (Observation Mode)

```bash
# Manage projects
python3 -m precompile projects add --id my_project --path /path/to/project --tools default
python3 -m precompile projects list
python3 -m precompile projects disable --id my_project
python3 -m precompile projects enable --id my_project
python3 -m precompile projects remove --id my_project

# Run the observation daemon
python3 -m precompile daemon --observe-only --pid-file /tmp/daemon.pid

# Check status and learned distributions
python3 -m precompile status
python3 -m precompile status --json

# Query prediction window and confidence gate diagnostics
python3 -m precompile predict --project my_project
```

---

## Benchmark Load Rules

Real CPU contention only occurs when background stress workloads and the build processes share the same physical/virtual cores. The benchmarking suite adheres to the following rules:

- **Core Pinning via taskset:** Both the load generator (`benchmarks/load.sh`) and developer simulation (`benchmarks/build_on_save.py`) must be pinned to the same CPU affinity set via `taskset -c $CPUSET` (environment variable `CPUSET`, default `0-1`).
- **Unpinned Daemon:** The PreCompile daemon (`precompile daemon`) must **NOT** be pinned to that CPU set. It runs unpinned across available cores to ensure monitoring and housekeeping are never starved by synthetic load.
- **Load Level Definitions:**
  - `idle`: 0 stress workers (clean baseline).
  - `low`: 1 CPU hog per CPU in the set (`hogs = 1 * num_cpus`).
  - `moderate`: 2 CPU hogs per CPU in the set (`hogs = 2 * num_cpus`).
  - `high`: 4 CPU hogs per CPU in the set (`hogs = 4 * num_cpus`).
- **Lifecycle:** Controlled via `benchmarks/load.sh [level|stop]` and calibrated via `benchmarks/calibrate_load.sh`.


---

## Cgroup v2 Hierarchical Sibling Placement Rule

In Linux cgroup v2, `cpu.weight` is strictly hierarchical: a cgroup's weight **only competes directly with its siblings** under the same parent directory.

To ensure elevated reservation priorities effectively boost compilation processes over background work during CPU contention:
- **Root Placement:** Reservation cgroups are created directly under the cgroup v2 root (`/sys/fs/cgroup/precompile-res-<id>`).
- **Sibling Competition:** In standard systemd environments (including WSL2), normal user tasks run in `/sys/fs/cgroup/init.scope` or `/sys/fs/cgroup/user.slice` with `cpu.weight = 100`. Because `precompile-res-<id>` is a direct sibling of `init.scope` under `/sys/fs/cgroup`, configuring `cpu.weight = 500` grants the reservation group a theoretical 5:1 proportional CPU share over ordinary work on any saturated core.
- **Safety Invariant:** `cpu.weight` is capped at 500 (default cap). Real-time scheduling classes (`SCHED_FIFO`, `SCHED_RR`) and `nice` levels are never modified. Non-root environments degrade safely to observation-only mode without errors.


---

## Project Structure & Stage Mapping

```
precompile/
├── cli.py         # Subcommands (daemon, status, projects, predict) - Stages 1-10
├── config.py      # Configuration & thresholds loader - Stage 10
├── monitor.py     # inotify save event watcher & debouncing - Stage 1
├── watcher.py     # Process creation detection (/proc, cn_proc) - Stage 2
├── matcher.py     # Executable tool & project root matcher - Stages 2 & 7
├── store.py       # SQLite database layer - Stages 3, 4, 8, 9
├── predictor.py   # EWMA latency learning & window calculation - Stages 3 & 4
├── gates.py       # Confidence gate & PSI CPU contention gate - Stages 4 & 5
├── reservation.py # Reservation state machine & atomic claim - Stages 6, 7, 8
├── cgroup.py      # Linux cgroup v2 controller - Stages 6 & 7
├── benefit.py     # Randomized holdouts & self-measured benefit - Stage 9
└── safety.py      # Harm bounding, budget caps & privilege checks - Stage 10
tests/
└── test_skeleton.py # Structural verification suite
```

---

## Daemon Actuation & A/B Smoke Benchmark (Stage 8)

The daemon can actuate priority reservations using Linux cgroup v2 when invoked with `--actuate` under root privileges:

```bash
# Run daemon in actuation mode with 50% randomized holdouts
wsl -u root python3 -m precompile.cli daemon --actuate --holdout-prob 0.5 --weight 500
```

### Key Actuation Mechanisms:
- **Watcher In-Place Exec Detection:** For newly spawned PIDs ($\le 0.5$s old), re-inspects executable symlinks on each scan cycle to detect commands executed via wrappers (e.g. `bash sleep -> exec gcc` or `taskset -c 0 make`).
- **Millisecond-Accurate Exit Timing:** Uses Linux `pidfd_open` with `select.poll` in a background monitor thread (`exit_monitor.py`), measuring build exit times with millisecond precision without process table polling delays.
- **Fixpoint Descendant Sweeper:** After claiming the root tool process (e.g., `make`), the reservation manager polls `/proc` in a fixpoint loop every 5ms for `sweep_window_s` (default 0.3s) to discover and migrate late-forked compiler children (e.g. `cc1`) into the boosted reservation group before setting `move_done`.
- **Claim/Tick Race Prevention:** Reservation records track a `move_done` flag so groups are not prematurely closed as "empty" before process and descendant migration completes.
- **PID Reuse Guard:** Re-reads field 22 (`starttime` in clock ticks) from `/proc/<pid>/stat` immediately before moving the process into the reservation cgroup; aborts migration if ticks differ.
- **Pruning:** Prunes completed reservation records in memory, preserving the 50 most recent records.

### Running the End-to-End A/B Benchmark:

```bash
# Runs 12 warm-up idle cycles followed by 60 contention cycles pinned to CPU set 0-1
wsl -u root bash benchmarks/ab_smoke.sh
```

---

## Closed-Loop Benefit Feedback & Priority Control (Stage 9)

PreCompile implements closed-loop priority feedback control to prevent priority inflation and ensure system resources are only allocated when real speedups occur:

- **Contention-Stratified Matching:** Compares `reserved` and `holdout` build durations across discrete contention bins (`[0, 10%)`, `[10, 30%)`, `[30, 60%)`, `[60, 100%]`).
- **Statistical Significance Testing:** Applies pure-Python Mann-Whitney U testing (with tie correction) per bin and combines significance across bins using Stouffer's weighted Z-score method.
- **Stratified Bootstrap Confidence Intervals:** Computes 95% confidence intervals on relative effect size using 1,000 bootstrap iterations.
- **Dynamic Weight Decay Sequence:** If speedup is statistically insignificant ($p \ge 0.05$) or negative, reservation priority decays stepwise:
  $$500 \to 250 \to 125 \to 100 \to \text{disabled}$$
- **Exploratory Probing:** When arming is disabled, exploratory probe reservations are issued with probability $p = 0.20$. If a probe detects significant benefit, priority steps back up.
- **Inspect Benefit State:**
  ```bash
  python3 -m precompile benefit --project my_project
  python3 -m precompile status
  ```
- **Demo Script:**
  ```bash
  wsl -u root bash benchmarks/benefit_demo.sh
  ```

---

## Installation, Deployment & System Changes

### Development Setup vs. Production Deployment
- **Development Setup (.pth pointer):**
  For cross-environment development on WSL2, a path pointer file is placed at `/usr/local/lib/python3.14/dist-packages/precompile.pth` containing `/mnt/c/Os Project`. This enables immediate live updates across Windows and WSL without reinstalling after every edit. Note: Windows DrvFs (`/mnt/c`) has known limitations with Linux `inotify` and timestamp resolution; watched project source trees must reside on native Linux ext4 partitions.
- **Production Deployment Routes:**
  For production deployment, install PreCompile directly onto native Linux ext4 storage:
  ```bash
  # Standard production install
  pip install .

  # Or editable developer install on native ext4
  pip install -e .
  ```

### External System Changes Outside Repository
The following files and paths outside the repository directory are utilized or modified by PreCompile:
1. `/usr/local/lib/python3.14/dist-packages/precompile.pth` - Python site-packages path configuration file for WSL2 development pointing to repo root.
2. `/etc/systemd/system/precompile.service` - System systemd service definition (copied from `packaging/precompile.service`).
3. `/etc/systemd/system/precompile.service.d/override.conf` - Drop-in configuration overrides used for test actuation.
4. `/var/lib/precompile/precompile.db` - System-wide SQLite database directory and database file for the systemd service.
5. `/run/precompile/`, `/run/precompile.pid` - Runtime PID file location.
6. `/run/precompile.pause`, `/var/run/precompile.pause` - Emergency kill-switch pause flag file locations.
7. `/sys/fs/cgroup/precompile-res-*` - Ephemeral cgroup v2 priority reservation control groups managed dynamically by the daemon.
8. `/home/saketh/precompile-demo-heavy` - Native ext4 C benchmark compilation workload directory.

---

## Documentation

- [SPEC_SUMMARY.md](SPEC_SUMMARY.md): Complete architecture, mathematical formulations, state machine diagrams, and validation protocol.
- [PLAN.md](PLAN.md): Step-by-step roadmap for all 11 implementation phases and their verification instructions.
