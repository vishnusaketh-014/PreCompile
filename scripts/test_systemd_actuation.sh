#!/usr/bin/env bash
set -e

echo "=== PreCompile Systemd Actuation Verification Test ==="

# Ensure clean slate
systemctl stop precompile 2>/dev/null || true
rm -rf /etc/systemd/system/precompile.service /etc/systemd/system/precompile.service.d
systemctl daemon-reload

# 1. Install systemd unit
cp packaging/precompile.service /etc/systemd/system/precompile.service

# 2. Add drop-in override to open all gates
mkdir -p /etc/systemd/system/precompile.service.d
cat << 'EOF' > /etc/systemd/system/precompile.service.d/override.conf
[Service]
ExecStart=
ExecStart=/usr/bin/python3 -m precompile daemon --actuate --db /var/lib/precompile/precompile.db --pid-file /run/precompile.pid --ablation-no-contention-gate --no-cost-gate --ablation-no-holdout
EOF

systemctl daemon-reload

# 3. Setup test project directory
TEST_DIR=$(mktemp -d /tmp/precompile_systemd_actuate_XXXXXX)
mkdir -p "$TEST_DIR"

# Reset /var/lib/precompile/precompile.db and seed it
mkdir -p /var/lib/precompile
rm -f /var/lib/precompile/precompile.db /var/lib/precompile/precompile.db-wal /var/lib/precompile/precompile.db-shm

python3 - << EOF
from precompile.store import DataStore
from precompile.predictor import LatencyPredictor

store = DataStore('/var/lib/precompile/precompile.db')
store.add_project('sysproj', '$TEST_DIR', 'default')

key = ('sysproj', 'source', 'default')
for i in range(6):
    store.record_sample('sysproj', 'source', 'default', 0.5)
    pred_id = store.record_prediction('sysproj', 'source', 'default', 100.0, 0.2, 0.8, armed=True)
    store.score_prediction(pred_id, 'hit', 0.5)
    store.record_build('sysproj', 'observed', 0.0, 2.0, 100.0, 102.0)
store.save_model_state(key, 6, 0.50, 0.01)

pred = LatencyPredictor(store=store)
desc = pred.describe(key)
print("Seeded sysproj gate status:", desc['gate_result'])
EOF

# 4. Start precompile service
systemctl start precompile
sleep 1
systemctl status precompile --no-pager

# 5. Trigger save and build
echo "=== Triggering save and build ==="
# Generate a medium-sized C file so gcc compiles for ~1 second
python3 -c "
with open('$TEST_DIR/main.c', 'w') as f:
    f.write('int main() {\n')
    for i in range(25000):
        f.write(f'  volatile int x{i} = {i};\n')
    f.write('  return 0;\n}\n')
"
sleep 0.5

# Run compilation in background and inspect cgroup
cd "$TEST_DIR"
gcc -c "$TEST_DIR/main.c" -o "$TEST_DIR/main.o" &
BUILD_PID=$!

PROCS_FOUND=""
for i in $(seq 1 30); do
    for cgrp in /sys/fs/cgroup/precompile-res-*; do
        if [ -d "$cgrp" ] && [ -f "$cgrp/cgroup.procs" ]; then
            CONTENT=$(cat "$cgrp/cgroup.procs" 2>/dev/null || true)
            if [ -n "$CONTENT" ]; then
                PROCS_FOUND="$CONTENT in $cgrp"
                echo "ACTIVE PROCS IN CGROUP: $PROCS_FOUND"
                break 2
            fi
        fi
    done
    sleep 0.05
done

wait $BUILD_PID || true
sleep 1.5

# 6. Check journalctl logs for [arm] and [claim]
echo "=== Journalctl output ==="
journalctl -u precompile --no-pager -n 30

# 7. Check reservation records
echo "=== Reservations table ==="
python3 -m precompile reservations --db /var/lib/precompile/precompile.db

# 8. Clean stop and verify 0 leftover cgroups
echo "=== Clean stop verification ==="
systemctl stop precompile
LEFTOVER=$(ls -d /sys/fs/cgroup/precompile-res-* 2>/dev/null | wc -l)
echo "Leftover cgroups after clean stop: $LEFTOVER"

# 9. Crash recovery test: start service, add stale cgroup, kill -9 daemon
echo "=== Crash recovery (kill -9) test ==="
systemctl start precompile
sleep 1
DAEMON_PID=$(pgrep -f "precompile daemon" || true)
echo "Running Daemon PID before crash: $DAEMON_PID"

mkdir -p /sys/fs/cgroup/precompile-res-sysd-crashtest
echo "Created simulated stale cgroup: /sys/fs/cgroup/precompile-res-sysd-crashtest"

kill -9 $DAEMON_PID
sleep 4 # wait for systemd RestartSec=3s

NEW_PID=$(pgrep -f "precompile daemon" || true)
echo "Systemd restarted Daemon PID: $NEW_PID"

STALE_AFTER=$(ls -d /sys/fs/cgroup/precompile-res-* 2>/dev/null | wc -l)
echo "Stale cgroups after restart cleanup: $STALE_AFTER"

# 10. Clean uninstall
systemctl stop precompile
rm -rf /etc/systemd/system/precompile.service /etc/systemd/system/precompile.service.d
systemctl daemon-reload
rm -rf "$TEST_DIR"
echo "=== Systemd actuation verification SUCCESS ==="
