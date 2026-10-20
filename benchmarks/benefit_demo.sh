#!/usr/bin/env bash
# benchmarks/benefit_demo.sh
# Closed-loop demonstration of Stage 9 Benefit Estimation & Priority Feedback Control.
#
# Phase 1 (Idle, 30 cycles): Builds run under idle conditions (zero contention benefit).
#   -> Verifies benefit estimator detects no benefit, decays weight (500 -> 250 -> 125 -> 100),
#      and transitions to 'disabled'.
#
# Phase 2 (Contention, 30 cycles): Starts high CPU contention load.
#   -> Verifies exploratory probes are issued, speedup is measured, and benefit estimator
#      re-enables arming and steps weight back up.

set -euo pipefail

CPUSET="${CPUSET:-0-1}"
PROJECT_DIR="${PROJECT_DIR:-/home/saketh/precompile-demo-heavy}"
DB_PATH="${DB_PATH:-/var/log/precompile/benefit_demo.db}"
DAEMON_PID_FILE="/var/log/precompile/benefit_demo.pid"
LOG_FILE="/var/log/precompile/benefit_demo.log"

echo "======================================================================"
echo "PreCompile Step 9: Closed-Loop Benefit Feedback Demo"
echo "  CPUSET:      ${CPUSET}"
echo "  Project:     ${PROJECT_DIR}"
echo "  Database:    ${DB_PATH}"
echo "  Log:         ${LOG_FILE}"
echo "======================================================================"

cleanup() {
    echo "Cleaning up processes..."
    ./benchmarks/load.sh stop >/dev/null 2>&1 || true
    if [ -f "${DAEMON_PID_FILE}" ]; then
        local pid
        pid=$(cat "${DAEMON_PID_FILE}" 2>/dev/null || true)
        if [ -n "${pid}" ]; then
            kill -TERM "${pid}" 2>/dev/null || true
            sleep 0.5
            kill -KILL "${pid}" 2>/dev/null || true
        fi
        rm -f "${DAEMON_PID_FILE}"
    fi
    pkill -f "precompile.cli daemon" 2>/dev/null || true
    python3 -m precompile.cli cleanup >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM

# Ensure root privileges
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: benchmarks/benefit_demo.sh requires root privileges."
    echo "Please invoke with: wsl -u root bash benchmarks/benefit_demo.sh"
    exit 1
fi

cleanup

rm -f "${DB_PATH}" "${DB_PATH}-wal" "${DB_PATH}-shm" "${LOG_FILE}"
mkdir -p "$(dirname "${DB_PATH}")"

# Verify heavy project exists
if [ ! -d "${PROJECT_DIR}" ]; then
    echo "Heavy project directory not found at ${PROJECT_DIR}. Generating..."
    python3 benchmarks/gen_heavy.py "${PROJECT_DIR}" 240
fi

make -C "${PROJECT_DIR}" clean >/dev/null 2>&1 || true
make -C "${PROJECT_DIR}" >/dev/null 2>&1

# Register project in database
python3 -m precompile.cli projects add --id heavy --path "${PROJECT_DIR}" --tools "gcc,make" --db "${DB_PATH}"

# Pre-seed latency model and baseline observed duration so confidence gate can_arm is True
echo "Pre-seeding baseline latency samples and observed duration..."
python3 -c "
from precompile.store import DataStore
store = DataStore('${DB_PATH}')
# Seed 6 latency samples around 1.8s
for _ in range(6):
    store.record_sample('heavy', 'c', 'default', 1.8, accepted=True)
# Seed 5 scored predictions as hits
for _ in range(5):
    pid = store.record_prediction('heavy', 'c', 'default', 0.0, 1.0, 2.5, False, False)
    store.score_prediction(pid, 'hit', 1.8)
# Seed 5 baseline observed build durations (around 0.35s)
for _ in range(5):
    store.record_build('heavy', 'observed', 0.0, 0.35, 0.0, 0.35)
store.close()
"

# Start daemon with actuation, 50% holdout, contention & cost gates disabled for Phase 1 (to test weight decay),
# and fast evaluation cadence for the demo (--eval-every 5 --min-total-n 5 --min-bin-n 5)
echo "Starting PreCompile daemon with closed-loop benefit estimation..."
python3 -m precompile.cli daemon \
    --actuate \
    --holdout-prob 0.5 \
    --weight 500 \
    --no-contention-gate \
    --no-cost-gate \
    --min-total-n 5 \
    --min-bin-n 5 \
    --eval-every 5 \
    --db "${DB_PATH}" \
    --pid-file "${DAEMON_PID_FILE}" \
    --log-level INFO > "${LOG_FILE}" 2>&1 &

sleep 1.5

echo ""
echo "=== Phase 1: Idle Contention (Decay Sequence: 500 -> 250 -> 125 -> 100 -> disabled) ==="
echo "Running idle cycles until arming is disabled by the controller..."

phase1_batch=0
while true; do
    phase1_batch=$((phase1_batch + 1))
    echo "--- Phase 1 Batch ${phase1_batch} (10 cycles) ---"
    python3 benchmarks/build_on_save.py --project "${PROJECT_DIR}" \
        --cycles 10 \
        --edit-file big.c \
        --delay-mean 1.8 \
        --delay-jitter 0.1 \
        --mode auto \
        --cpuset "${CPUSET}"

    # Check benefit state
    state_status=$(python3 -c "
import json
from precompile.store import DataStore
store = DataStore('${DB_PATH}')
st = store.get_benefit_state('heavy')
print(st['status'] if st else 'unknown')
store.close()
")
    state_weight=$(python3 -c "
from precompile.store import DataStore
store = DataStore('${DB_PATH}')
st = store.get_benefit_state('heavy')
print(st['weight'] if st else 'unknown')
store.close()
")
    echo "Current status: ${state_status}, weight: ${state_weight}"
    if [ "${state_status}" = "disabled" ]; then
        echo "Phase 1 Goal Reached: Controller has reached status=disabled (weight=${state_weight})"
        break
    fi
    if [ ${phase1_batch} -ge 8 ]; then
        echo "Warning: Reached maximum Phase 1 batches (${phase1_batch}). Proceeding."
        break
    fi
done

echo ""
echo "--- Benefit State After Phase 1 ---"
python3 -m precompile.cli benefit --project heavy --db "${DB_PATH}"

echo ""
echo "=== Phase 2: High Contention (Probe -> Re-enable & Step Up) ==="
echo "Starting heavy load pinned to ${CPUSET}..."
CPUSET="${CPUSET}" ./benchmarks/load.sh high
echo "Waiting 15 s for PSI to ramp..."
sleep 15

echo "Running cycles under contention (probes will detect real boost)..."
phase2_batch=0
while true; do
    phase2_batch=$((phase2_batch + 1))
    echo "--- Phase 2 Batch ${phase2_batch} (10 cycles) ---"
    python3 benchmarks/build_on_save.py --project "${PROJECT_DIR}" \
        --cycles 10 \
        --edit-file big.c \
        --delay-mean 1.8 \
        --delay-jitter 0.1 \
        --mode auto \
        --cpuset "${CPUSET}"

    state_status=$(python3 -c "
from precompile.store import DataStore
store = DataStore('${DB_PATH}')
st = store.get_benefit_state('heavy')
print(st['status'] if st else 'unknown')
store.close()
")
    state_weight=$(python3 -c "
from precompile.store import DataStore
store = DataStore('${DB_PATH}')
st = store.get_benefit_state('heavy')
print(st['weight'] if st else 'unknown')
store.close()
")
    echo "Current status: ${state_status}, weight: ${state_weight}"
    if [ "${state_status}" != "disabled" ] && [ "${state_weight}" -gt 100 ]; then
        echo "Phase 2 Goal Reached: Controller re-enabled arming and stepped weight up to ${state_weight}!"
        break
    fi
    if [ ${phase2_batch} -ge 6 ]; then
        echo "Phase 2 reached batch limit (${phase2_batch}). Proceeding."
        break
    fi
done

echo ""
echo "Stopping load..."
./benchmarks/load.sh stop

echo ""
echo "=== Final Status & Benefit Evaluations ==="
python3 -m precompile.cli status --db "${DB_PATH}"
echo ""
python3 -m precompile.cli benefit --project heavy --db "${DB_PATH}"
