#!/usr/bin/env bash
# benchmarks/load.sh
# Usage: ./load.sh [idle|low|moderate|high|stop]
# Environment: CPUSET (default: 0-1)

set -euo pipefail

LEVEL="${1:-}"
CPUSET="${CPUSET:-0-1}"
PID_FILE="/tmp/precompile_stress.pid"

if [ -z "${LEVEL}" ]; then
    echo "Usage: $0 [idle|low|moderate|high|stop]"
    echo "Environment: CPUSET (default: 0-1, current: ${CPUSET})"
    exit 1
fi

stop_load() {
    if [ -f "${PID_FILE}" ]; then
        local pid
        pid=$(cat "${PID_FILE}" 2>/dev/null || true)
        if [ -n "${pid}" ]; then
            kill -TERM "${pid}" 2>/dev/null || true
            sleep 0.5
            kill -KILL "${pid}" 2>/dev/null || true
        fi
        rm -f "${PID_FILE}"
    fi
    pkill -f "stress-ng.*--cpu" 2>/dev/null || true
}

if [ "${LEVEL}" = "stop" ]; then
    stop_load
    echo "Load stopped."
    exit 0
fi

if [ "${LEVEL}" = "idle" ]; then
    stop_load
    echo "Load set to idle (0 stress-ng workers)."
    exit 0
fi

# Compute number of CPUs in CPUSET
NUM_CPUS=$(python3 -c '
import sys
s = sys.argv[1]
cpus = set()
for part in s.split(","):
    part = part.strip()
    if not part:
        continue
    if "-" in part:
        lo, hi = map(int, part.split("-"))
        cpus.update(range(lo, hi + 1))
    else:
        cpus.add(int(part))
print(len(cpus) if cpus else 1)
' "${CPUSET}")

case "${LEVEL}" in
    low)
        HOGS_PER_CPU=1
        ;;
    moderate)
        HOGS_PER_CPU=2
        ;;
    high)
        HOGS_PER_CPU=4
        ;;
    *)
        echo "Error: Unknown level '${LEVEL}'. Expected: idle, low, moderate, high, stop"
        exit 1
        ;;
esac

TOTAL_HOGS=$(( NUM_CPUS * HOGS_PER_CPU ))

stop_load

echo "Starting stress-ng: level=${LEVEL}, cpuset=${CPUSET} (${NUM_CPUS} CPUs), hogs=${TOTAL_HOGS} (${HOGS_PER_CPU}/CPU)"
taskset -c "${CPUSET}" stress-ng --cpu "${TOTAL_HOGS}" --timeout 3600s >/dev/null 2>&1 &
STRESS_PID=$!
echo "${STRESS_PID}" > "${PID_FILE}"
sleep 0.5

if ! kill -0 "${STRESS_PID}" 2>/dev/null; then
    echo "Error: stress-ng failed to start"
    rm -f "${PID_FILE}"
    exit 1
fi

echo "stress-ng running with PID ${STRESS_PID} on CPU set ${CPUSET}"
