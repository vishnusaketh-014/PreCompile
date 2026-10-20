#!/usr/bin/env bash
# benchmarks/ab_smoke.sh
# End-to-end A/B smoke test validating priority actuation under heavy CPU contention.
# Compares build durations for "reserved" (boosted weight 500) vs "holdout" (standard weight 100).

set -euo pipefail

CPUSET="${CPUSET:-0-1}"
PROJECT_DIR="${PROJECT_DIR:-/home/saketh/precompile-demo-heavy}"
DB_PATH="${DB_PATH:-/tmp/ab_smoke.db}"
DAEMON_PID_FILE="/tmp/ab_smoke_daemon.pid"
HARNESS_JSONL="/tmp/ab_smoke_harness.jsonl"
CYCLES="${CYCLES:-60}"
WARMUP_CYCLES="${WARMUP_CYCLES:-12}"

echo "======================================================================"
echo "PreCompile Step 8b: Real A/B Smoke Benchmark"
echo "  CPUSET:          ${CPUSET}"
echo "  Project:         ${PROJECT_DIR}"
echo "  Database:        ${DB_PATH}"
echo "  Warm-up Cycles:  ${WARMUP_CYCLES} (idle)"
echo "  A/B Test Cycles: ${CYCLES} (high load)"
echo "======================================================================"

cleanup() {
    echo "Cleaning up processes and background tasks..."
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
    echo "ERROR: benchmarks/ab_smoke.sh requires root privileges to manage cgroup v2."
    echo "Please invoke with: wsl -u root bash benchmarks/ab_smoke.sh"
    exit 1
fi

cleanup

# Prepare directory and clean database
rm -f "${DB_PATH}" "${DB_PATH}-wal" "${DB_PATH}-shm" "${HARNESS_JSONL}"
mkdir -p "$(dirname "${DB_PATH}")"

# Verify heavy project exists
if [ ! -d "${PROJECT_DIR}" ]; then
    echo "Heavy project directory not found at ${PROJECT_DIR}. Generating now..."
    python3 benchmarks/gen_heavy.py "${PROJECT_DIR}" 240
fi

# Ensure initial build is clean and ready
echo "Building initial project artifacts in ${PROJECT_DIR}..."
make -C "${PROJECT_DIR}" clean >/dev/null 2>&1 || true
make -C "${PROJECT_DIR}" >/dev/null 2>&1

# Register project in database
echo "Registering heavy project in PreCompile store..."
python3 -m precompile.cli projects add --id heavy --path "${PROJECT_DIR}" --tools "gcc,make" --db "${DB_PATH}"

# Start PreCompile Daemon with actuation and 50% holdout rate (unpinned)
echo "Starting PreCompile daemon (--actuate, --holdout-prob 0.5)..."
python3 -m precompile.cli daemon \
    --actuate \
    --holdout-prob 0.5 \
    --weight 500 \
    --db "${DB_PATH}" \
    --pid-file "${DAEMON_PID_FILE}" \
    --log-level INFO \
    ${EXTRA_DAEMON_ARGS:-} &

DAEMON_PID=$!
sleep 1.5

# Step 1: Run 10 warm-up cycles at idle to learn event latency and establish observed build duration
echo ""
echo "--- Phase 1: Warm-up (${WARMUP_CYCLES} idle cycles) ---"
python3 benchmarks/build_on_save.py --project "${PROJECT_DIR}" \
    --cycles "${WARMUP_CYCLES}" \
    --edit-file big.c \
    --delay-mean 1.8 \
    --delay-jitter 0.1 \
    --mode auto \
    --cpuset "${CPUSET}"

echo "Warm-up complete. Status:"
python3 -m precompile.cli status --db "${DB_PATH}"

# Step 2: Start high contention pinned load
echo ""
echo "--- Phase 2: Starting High Contention Load pinned to ${CPUSET} ---"
CPUSET="${CPUSET}" ./benchmarks/load.sh high
echo "Waiting 15s for PSI avg10 to ramp..."
sleep 15

if [ -f /proc/pressure/cpu ]; then
    echo "CPU PSI under load:"
    cat /proc/pressure/cpu
fi

# Step 3: Run 60 A/B test cycles under high load
echo ""
echo "--- Phase 3: Running A/B Test (${CYCLES} cycles under load) ---"
python3 benchmarks/build_on_save.py --project "${PROJECT_DIR}" \
    --cycles "${CYCLES}" \
    --edit-file big.c \
    --delay-mean 1.8 \
    --delay-jitter 0.1 \
    --mode auto \
    --cpuset "${CPUSET}" \
    --log-jsonl "${HARNESS_JSONL}"

# Step 4: Stop load and daemon cleanly
echo ""
echo "--- Phase 4: Stopping Load and Daemon ---"
./benchmarks/load.sh stop
if [ -f "${DAEMON_PID_FILE}" ]; then
    DAEMON_PID=$(cat "${DAEMON_PID_FILE}")
    kill -TERM "${DAEMON_PID}" 2>/dev/null || true
    wait "${DAEMON_PID}" 2>/dev/null || true
    rm -f "${DAEMON_PID_FILE}"
fi
sleep 1.0

# Step 5: Output PreCompile status and analysis
echo ""
echo "--- Phase 5: Results & Statistical Analysis ---"
python3 -m precompile.cli status --db "${DB_PATH}"
python3 -m precompile.cli reservations --db "${DB_PATH}"

python3 benchmarks/analyze_ab.py --db "${DB_PATH}"
echo "A/B Smoke Benchmark Complete."
