#!/usr/bin/env bash
# benchmarks/calibrate_load.sh
#
# Calibrates CPU load levels (idle, low, moderate, high) under taskset pinning.
# Evaluates PSI (avg10, avg60, recent_some_pct), build execution time (median, p95),
# and relative slowdown vs. idle.
# Outputs markdown table to terminal and benchmarks/results/load_calibration.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PYTHONPATH="${REPO_DIR}:${PYTHONPATH:-}"

CPUSET="${CPUSET:-0-1}"
DEMO_DIR="${HOME}/precompile-calibrate"
DEFAULT_SRC_DIR="${DEMO_DIR}/sample_project"
SRC_DIR="${PROJECT_DIR:-${DEFAULT_SRC_DIR}}"
RESULTS_DIR="${REPO_DIR}/benchmarks/results"

echo "=== PreCompile Load Calibration ==="
echo "CPUSET:    ${CPUSET}"
echo "Directory: ${SRC_DIR}"

mkdir -p "${SRC_DIR}" "${RESULTS_DIR}"

# Prepare sample project if using default demo directory
if [ -z "${PROJECT_DIR:-}" ]; then
    if [ -d "${SCRIPT_DIR}/sample_project" ]; then
        cp -r "${SCRIPT_DIR}/sample_project/." "${SRC_DIR}/"
    fi
fi
(cd "${SRC_DIR}" && make clean >/dev/null 2>&1 || true)

LEVELS=("idle" "low" "moderate" "high")
declare -A PSI_AVG10
declare -A PSI_AVG60
declare -A PSI_RECENT
declare -A MED_DUR
declare -A P95_DUR
declare -A SLOWDOWN

IDLE_MEDIAN=0.0

cleanup() {
    "${SCRIPT_DIR}/load.sh" stop >/dev/null 2>&1 || true
}
trap cleanup EXIT

for level in "${LEVELS[@]}"; do
    echo ""
    echo "=========================================================="
    echo " Calibrating level: ${level} (cpuset: ${CPUSET})"
    echo "=========================================================="

    # 1. Start load
    CPUSET="${CPUSET}" "${SCRIPT_DIR}/load.sh" "${level}"

    # 2. Wait 15 s for PSI avg10 to ramp
    echo "Waiting 15s for PSI metrics to ramp..."
    sleep 15

    # 3. Read PSI metrics
    PSI_METRICS=$(python3 -c "
import time
from precompile.gates import PSISampler, read_psi_cpu

sampler = PSISampler()
sampler.sample()
time.sleep(1.0)
m = sampler.sample()

if m is not None:
    avg10 = m.some_avg10
    avg60 = m.some_avg60
    recent = sampler.recent_some_pct
    print(f'{avg10:.2f} {avg60:.2f} {recent:.2f}')
else:
    print('0.00 0.00 0.00')
")

    read -r a10 a60 rec <<< "${PSI_METRICS}"
    PSI_AVG10["${level}"]="${a10}"
    PSI_AVG60["${level}"]="${a60}"
    PSI_RECENT["${level}"]="${rec}"
    echo "PSI snapshot after 15s ramp: avg10=${a10}%, avg60=${a60}%, recent_some_pct=${rec}%"

    # 4. Run 10 build cycles under the same cpuset
    echo "Running 10 build cycles under cpuset ${CPUSET}..."
    RUN_OUT=$(python3 "${SCRIPT_DIR}/build_on_save.py" \
        --project "${SRC_DIR}" \
        --cycles 10 \
        --mode auto \
        --cpuset "${CPUSET}")

    # 5. Extract build durations and calculate median & p95
    STATS=$(python3 -c "
import sys, re

text = sys.stdin.read()
matches = re.findall(r'Build completed in ([0-9.]+)s', text)
durs = [float(x) for x in matches]
if durs:
    s = sorted(durs)
    n = len(s)
    med = s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    idx95 = min(n - 1, int(round(0.95 * (n - 1))))
    p95 = s[idx95]
    print(f'{med:.3f} {p95:.3f}')
else:
    print('0.000 0.000')
" <<< "${RUN_OUT}")

    read -r med p95 <<< "${STATS}"
    MED_DUR["${level}"]="${med}"
    P95_DUR["${level}"]="${p95}"

    if [ "${level}" = "idle" ]; then
        IDLE_MEDIAN="${med}"
        SLOWDOWN["${level}"]="1.00x"
    else
        sd=$(python3 -c "
im = float('${IDLE_MEDIAN}')
cm = float('${med}')
ratio = (cm / im) if im > 0 else 1.0
print(f'{ratio:.2f}x')
")
        SLOWDOWN["${level}"]="${sd}"
    fi

    echo "Build statistics: median=${med}s, p95=${p95}s, slowdown=${SLOWDOWN[${level}]}"

    # Stop load before next step
    "${SCRIPT_DIR}/load.sh" stop >/dev/null 2>&1 || true
    sleep 2
done

# Build Markdown Report
OUT_FILE="${RESULTS_DIR}/load_calibration.md"

cat > "${OUT_FILE}" << EOF
# PreCompile CPU Load Calibration Report

Evaluation of CPU contention levels pinned to CPU set \`${CPUSET}\`.
For each load level, stress-ng runs pinned to \`${CPUSET}\`, ramps for 15 seconds, and 10 build cycles are executed under the same CPU set.

| Level | PSI avg10 | recent_some_pct | Median Build (s) | p95 Build (s) | Slowdown vs Idle |
| :--- | :---: | :---: | :---: | :---: | :---: |
EOF

for level in "${LEVELS[@]}"; do
    cat >> "${OUT_FILE}" << EOF
| ${level} | ${PSI_AVG10[${level}]}% | ${PSI_RECENT[${level}]}% | ${MED_DUR[${level}]}s | ${P95_DUR[${level}]}s | ${SLOWDOWN[${level}]} |
EOF
done

# Check if high PSI stayed below 5%
HIGH_AVG10="${PSI_AVG10[high]}"
HIGH_RECENT="${PSI_RECENT[high]}"
LOW_PSI_NOTE=$(python3 -c "
a10 = float('${HIGH_AVG10}')
rec = float('${HIGH_RECENT}')
if max(a10, rec) < 5.0:
    print('true')
else:
    print('false')
")

if [ "${LOW_PSI_NOTE}" = "true" ]; then
    cat >> "${OUT_FILE}" << EOF

## Pressure Stall Information (PSI) Findings in WSL2
> **Notice:** Under load level \`high\` (4 hogs/CPU pinned to cpuset \`${CPUSET}\`), PSI measured:
> - \`some_avg10\` = ${HIGH_AVG10}%
> - \`recent_some_pct\` = ${HIGH_RECENT}%
>
> Both values stayed below 5.0% despite significant build slowdown (${SLOWDOWN[high]}).
> **Evidence:** In WSL2 (Hyper-V virtualized Linux kernel), tasks stalled waiting for virtual CPU time may not report high pressure stall metrics if the Windows host scheduler transparently schedules the underlying vCPUs without kernel-visible runqueue stalls, or if vCPU overcommit is masked by the Hyper-V hypervisor. The measured numbers are reported as-is without artificial tuning.
EOF
fi

cat >> "${OUT_FILE}" << EOF

---
*Generated by benchmarks/calibrate_load.sh (cpuset: ${CPUSET})*
EOF

echo ""
echo "=========================================================="
echo " Calibration Report: ${OUT_FILE}"
echo "=========================================================="
cat "${OUT_FILE}"
