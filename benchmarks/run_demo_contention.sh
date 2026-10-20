#!/usr/bin/env bash
# benchmarks/run_demo_contention.sh
#
# Step 7b: Real contention demonstration with pinned CPU load and unpinned daemon.
#
# Workload & Contention setup:
#   - Pinned CPU set: $CPUSET (default 0-1)
#   - Daemon runs UNPINNED (must not be pinned to the workload CPU set)
#   - Phase 1 (Idle): 10 builds pinned to $CPUSET, load=idle
#   - Phase 2 (Contention): load=high (4 hogs/CPU pinned to $CPUSET), 15s ramp wait, 14 builds pinned to $CPUSET
#
# Reports:
#   - PSI (avg10, avg60, recent_some_pct) at each phase
#   - Median build time per phase
#   - precompile status with decision counts
#   - [arm?] decision log lines

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PYTHONPATH="${REPO_DIR}:${PYTHONPATH:-}"

CPUSET="${CPUSET:-0-1}"
DEMO_DIR="${HOME}/precompile-demo"
DB_PATH="${DEMO_DIR}/precompile.db"
PID_FILE="${DEMO_DIR}/daemon.pid"
LOG_FILE="${DEMO_DIR}/daemon.log"
SRC_DIR="${DEMO_DIR}/sample_project"
PRECOMPILE="python3 -m precompile"

echo "=== PreCompile Contention Demo (Step 7b) ==="
echo "Workload cpuset: ${CPUSET}"
echo "Demo dir:        ${DEMO_DIR}"

# ── Cleanup on exit ────────────────────────────────────────────────────────────
cleanup() {
    "${SCRIPT_DIR}/load.sh" stop >/dev/null 2>&1 || true
    if [ -f "${PID_FILE}" ]; then
        kill -TERM "$(cat "${PID_FILE}")" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ── Setup ──────────────────────────────────────────────────────────────────────
mkdir -p "${SRC_DIR}"
rm -f "${DB_PATH}" "${LOG_FILE}" "${PID_FILE}"

if [ -d "${SCRIPT_DIR}/sample_project" ]; then
    cp -r "${SCRIPT_DIR}/sample_project/." "${SRC_DIR}/"
fi
(cd "${SRC_DIR}" && make clean >/dev/null 2>&1 || true)

# Register project
echo ""
echo "--- Registering project ---"
${PRECOMPILE} projects add \
    --id demo \
    --path "${SRC_DIR}" \
    --db "${DB_PATH}" \
    --pid-file "${PID_FILE}"

# ── Start Daemon (UNPINNED) ────────────────────────────────────────────────────
echo ""
echo "--- Starting daemon (UNPINNED) ---"
${PRECOMPILE} daemon \
    --db "${DB_PATH}" \
    --pid-file "${PID_FILE}" \
    --log-level INFO \
    > "${LOG_FILE}" 2>&1 &
DAEMON_PID=$!

deadline=$((SECONDS + 5))
while [ ! -f "${PID_FILE}" ] && [ $SECONDS -lt $deadline ]; do
    sleep 0.2
done

if [ ! -f "${PID_FILE}" ]; then
    echo "ERROR: Daemon did not start within 5s"
    exit 1
fi
echo "Daemon started (PID $(cat "${PID_FILE}"))"

# ── PSI Reader Helper ──────────────────────────────────────────────────────────
read_psi_snapshot() {
    local label="$1"
    python3 -c "
import sys, time
from precompile.gates import PSISampler, read_psi_cpu

label = sys.argv[1]
sampler = PSISampler()
sampler.sample()
time.sleep(1.0)
m = sampler.sample()

if m is not None:
    print(f'[{label}] avg10={m.some_avg10:.2f}%, avg60={m.some_avg60:.2f}%, recent_some_pct={sampler.recent_some_pct:.2f}% (total_us={m.some_total_us})')
else:
    print(f'[{label}] PSI not available')
" "${label}"
}

compute_median_duration() {
    local output="$1"
    python3 -c "
import sys, re
text = sys.stdin.read()
matches = re.findall(r'Build completed in ([0-9.]+)s', text)
durs = [float(x) for x in matches]
if durs:
    s = sorted(durs)
    n = len(s)
    med = s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    print(f'{med:.3f}')
else:
    print('0.000')
" <<< "${output}"
}

# ── Phase 1: Idle (10 build cycles under cpuset) ───────────────────────────────
echo ""
echo "=========================================================="
echo " Phase 1: Idle (10 cycles pinned to cpuset ${CPUSET})"
echo "=========================================================="
CPUSET="${CPUSET}" "${SCRIPT_DIR}/load.sh" idle
PSI_IDLE_START=$(read_psi_snapshot "idle start")
echo "${PSI_IDLE_START}"

IDLE_OUT=$(python3 "${SCRIPT_DIR}/build_on_save.py" \
    --project "${SRC_DIR}" \
    --cycles 10 \
    --mode auto \
    --cpuset "${CPUSET}")
echo "${IDLE_OUT}"

IDLE_MEDIAN=$(compute_median_duration "${IDLE_OUT}")
PSI_IDLE_END=$(read_psi_snapshot "idle end")
echo "${PSI_IDLE_END}"
echo "Median idle build duration: ${IDLE_MEDIAN}s"

# ── Phase 2: Contention (high load pinned to cpuset, 15s ramp, 14 cycles) ──────
echo ""
echo "=========================================================="
echo " Phase 2: Contention (load=high pinned to ${CPUSET}, 14 cycles)"
echo "=========================================================="
CPUSET="${CPUSET}" "${SCRIPT_DIR}/load.sh" high

echo "Waiting 15s for PSI metrics to ramp..."
sleep 15
PSI_RAMPED=$(read_psi_snapshot "post-ramp")
echo "${PSI_RAMPED}"

CONTENTION_OUT=$(python3 "${SCRIPT_DIR}/build_on_save.py" \
    --project "${SRC_DIR}" \
    --cycles 14 \
    --mode auto \
    --cpuset "${CPUSET}")
echo "${CONTENTION_OUT}"

CONTENTION_MEDIAN=$(compute_median_duration "${CONTENTION_OUT}")
PSI_CONTENTION_END=$(read_psi_snapshot "contention end")
echo "${PSI_CONTENTION_END}"
echo "Median contention build duration: ${CONTENTION_MEDIAN}s"

# Stop load
"${SCRIPT_DIR}/load.sh" stop
sleep 1

# ── Stop daemon cleanly ────────────────────────────────────────────────────────
echo ""
echo "--- Stopping daemon ---"
if [ -f "${PID_FILE}" ]; then
    kill -TERM "$(cat "${PID_FILE}")" 2>/dev/null || true
    sleep 1
fi
wait "${DAEMON_PID}" 2>/dev/null || true

# ── Report Summary ─────────────────────────────────────────────────────────────
echo ""
echo "=========================================================="
echo " DEMO SUMMARY & REPORT"
echo "=========================================================="
echo "Workload cpuset: ${CPUSET}"
echo ""
echo "Phase 1 (Idle) PSI & Build Time:"
echo "  ${PSI_IDLE_START}"
echo "  ${PSI_IDLE_END}"
echo "  Median Build Duration: ${IDLE_MEDIAN}s"
echo ""
echo "Phase 2 (Contention) PSI & Build Time:"
echo "  ${PSI_RAMPED}"
echo "  ${PSI_CONTENTION_END}"
echo "  Median Build Duration: ${CONTENTION_MEDIAN}s"
SLOWDOWN=$(python3 -c "
im = float('${IDLE_MEDIAN}')
cm = float('${CONTENTION_MEDIAN}')
print(f'{(cm/im):.2f}x' if im > 0 else 'N/A')
")
echo "  Build Slowdown under Load: ${SLOWDOWN}"

echo ""
echo "=== precompile status ==="
${PRECOMPILE} status --db "${DB_PATH}"

echo ""
echo "=== [arm?] log lines ==="
if grep -q "\[arm?\]" "${LOG_FILE}"; then
    grep "\[arm?\]" "${LOG_FILE}"
else
    echo "(no [arm?] lines found in ${LOG_FILE})"
fi

echo ""
echo "=== Verification of Arming Decisions ==="
python3 -c "
import sqlite3
conn = sqlite3.connect('${DB_PATH}')
cur = conn.cursor()
cur.execute('SELECT decision, contention_level, benefit_s, cost_s FROM predictions WHERE decision IS NOT NULL')
rows = cur.fetchall()
conn.close()

arm_count = sum(1 for r in rows if r[0] == 'would_arm')
holdout_count = sum(1 for r in rows if r[0] == 'holdout')
no_contention_count = sum(1 for r in rows if r[0] == 'abstain_no_contention')
conf_count = sum(1 for r in rows if r[0] == 'abstain_confidence')

print(f'Total decisions logged: {len(rows)}')
print(f'  would_arm:              {arm_count}')
print(f'  holdout:                {holdout_count}')
print(f'  abstain_no_contention:  {no_contention_count}')
print(f'  abstain_confidence:     {conf_count}')

if arm_count > 0 or holdout_count > 0:
    print('SUCCESS: Contention gate passed; would_arm and/or holdout decisions recorded.')
else:
    print('ANALYSIS: No would_arm or holdout decisions triggered.')
    if rows:
        max_psi = max(r[1] for r in rows)
        print(f'  Max PSI contention_level observed at decision points: {max_psi:.2f}%')
        print(f'  Configured contention threshold: 10.0%')
        print(f'  Reason: PSI ({max_psi:.2f}%) did not exceed threshold (10.0%).')
"

echo ""
echo "=== Demo Complete ==="
echo "Log file: ${LOG_FILE}"
echo "Database: ${DB_PATH}"
