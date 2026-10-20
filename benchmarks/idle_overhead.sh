#!/usr/bin/env bash
# benchmarks/idle_overhead.sh
# Measure idle actuation overhead under zero contention:
# 40 cycles with contention & cost gates disabled, holdout_prob=0.5,
# weight fixed at 500 (benefit controller disabled), leak_check=False.

set -euo pipefail

CPUSET="${CPUSET:-0-1}"
PROJECT_DIR="${PROJECT_DIR:-/home/saketh/precompile-demo-heavy}"
DB_PATH="/var/log/precompile/idle_overhead.db"
DAEMON_PID_FILE="/var/log/precompile/idle_overhead.pid"
LOG_FILE="/var/log/precompile/idle_overhead.log"

echo "======================================================================"
echo "PreCompile Step 9b: Idle Overhead Benchmark (Bounded-Harm Test)"
echo "  CPUSET:      ${CPUSET}"
echo "  Project:     ${PROJECT_DIR}"
echo "  Database:    ${DB_PATH}"
echo "  Log:         ${LOG_FILE}"
echo "======================================================================"

cleanup() {
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

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: benchmarks/idle_overhead.sh requires root privileges."
    exit 1
fi

cleanup

rm -f "${DB_PATH}" "${DB_PATH}-wal" "${DB_PATH}-shm" "${LOG_FILE}"
mkdir -p "$(dirname "${DB_PATH}")"

# Register project in database
python3 -m precompile.cli projects add --id heavy --path "${PROJECT_DIR}" --tools "gcc,make" --db "${DB_PATH}"

# Pre-seed latency model so confidence gate can_arm is True
python3 -c "
from precompile.store import DataStore
store = DataStore('${DB_PATH}')
for _ in range(6):
    store.record_sample('heavy', 'c', 'default', 1.8, accepted=True)
for _ in range(5):
    pid = store.record_prediction('heavy', 'c', 'default', 0.0, 1.0, 2.5, False, False)
    store.score_prediction(pid, 'hit', 1.8)
for _ in range(5):
    store.record_build('heavy', 'observed', 0.0, 0.35, 0.0, 0.35)
store.close()
"

# Start daemon with fixed weight 500, holdout_prob=0.5, no contention gate, no cost gate,
# benefit controller disabled (eval-every very large so it never evaluates), leak-check OFF
echo "Starting daemon with actuation, fixed weight 500, leak-check OFF..."
python3 -m precompile.cli daemon \
    --actuate \
    --holdout-prob 0.5 \
    --weight 500 \
    --no-contention-gate \
    --no-cost-gate \
    --eval-every 100000 \
    --db "${DB_PATH}" \
    --pid-file "${DAEMON_PID_FILE}" \
    --log-level INFO > "${LOG_FILE}" 2>&1 &

sleep 1.5

echo "Running 40 cycles at idle..."
python3 benchmarks/build_on_save.py --project "${PROJECT_DIR}" \
    --cycles 40 \
    --edit-file big.c \
    --delay-mean 1.8 \
    --delay-jitter 0.1 \
    --mode auto \
    --cpuset "${CPUSET}"

echo ""
echo "=== Idle Overhead Analysis ==="
PYTHONPATH=. python3 -c "
import sqlite3
import statistics
from precompile.stats import mann_whitney_u, bootstrap_relative_effect_ci

conn = sqlite3.connect('${DB_PATH}')
conn.row_factory = sqlite3.Row
rows = conn.execute(\"SELECT condition, duration_s FROM builds WHERE condition IN ('reserved', 'holdout')\").fetchall()
conn.close()

res = [r['duration_s'] for r in rows if r['condition'] == 'reserved']
hld = [r['duration_s'] for r in rows if r['condition'] == 'holdout']

def p95(arr):
    s = sorted(arr)
    k = (len(s) - 1) * 0.95
    f = int(k)
    c = min(f + 1, len(s) - 1)
    d = k - f
    return s[f] + d * (s[c] - s[f])

print(f'Reserved (n={len(res)}): median={statistics.median(res):.3f}s, mean={statistics.mean(res):.3f}s, p95={p95(res):.3f}s')
print(f'Holdout  (n={len(hld)}): median={statistics.median(hld):.3f}s, mean={statistics.mean(hld):.3f}s, p95={p95(hld):.3f}s')

u, u_norm, z, p = mann_whitney_u(res, hld)
r_by_bin = {'idle': res}
h_by_bin = {'idle': hld}
eff, ci_low, ci_high = bootstrap_relative_effect_ci(r_by_bin, h_by_bin, n_resamples=2000, alpha=0.05)
print(f'Relative effect (1 - res/hld): {eff*100:+.2f}%')
print(f'95% Bootstrap CI: [{ci_low*100:+.2f}%, {ci_high*100:+.2f}%]')
print(f'Mann-Whitney U p-value: {p:.4f}')
"
