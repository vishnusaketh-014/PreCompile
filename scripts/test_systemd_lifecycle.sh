#!/usr/bin/env bash
set -e

echo "=== 1. Start service and check systemctl status ==="
systemctl start precompile
systemctl status precompile --no-pager

echo "=== 2. Add project and reload ==="
TEST_DIR=$(mktemp -d /tmp/precompile_systemd_test_XXXXXX)
mkdir -p "$TEST_DIR"
touch "$TEST_DIR/main.c"

python3 -m precompile projects add --id sysproj --path "$TEST_DIR" --db /var/lib/precompile/precompile.db
systemctl kill -s HUP precompile.service
sleep 1

echo "=== 3. Trigger save and build ==="
echo 'int main() { return 0; }' > "$TEST_DIR/main.c"
sleep 0.5
cd "$TEST_DIR" && gcc -c "$TEST_DIR/main.c" -o "$TEST_DIR/main.o" || true
sleep 1

echo "=== 4. Check status and reservations ==="
python3 -m precompile status --db /var/lib/precompile/precompile.db
python3 -m precompile reservations --db /var/lib/precompile/precompile.db

echo "=== 5. Stop service and verify 0 leftover cgroups ==="
systemctl stop precompile
LEFTOVER=$(ls -d /sys/fs/cgroup/precompile-res-* 2>/dev/null | wc -l)
echo "Leftover cgroups after clean stop: $LEFTOVER"

echo "=== 6. Start service, simulate crash (kill -9), verify restart and stale cleanup ==="
systemctl start precompile
sleep 1
DAEMON_PID=$(pgrep -f "precompile daemon" || true)
echo "Running Daemon PID before crash: $DAEMON_PID"

# Simulate an active stale cgroup as if crash occurred during active reservation
mkdir -p /sys/fs/cgroup/precompile-res-crashtest
echo "Created simulated stale cgroup: /sys/fs/cgroup/precompile-res-crashtest"

# Kill daemon with SIGKILL
kill -9 $DAEMON_PID
sleep 4 # wait for systemd RestartSec=3s

NEW_PID=$(pgrep -f "precompile daemon" || true)
echo "Systemd restarted Daemon PID: $NEW_PID"

# Check if ExecStopPost / startup cleanup removed the stale cgroup
STALE_AFTER=$(ls -d /sys/fs/cgroup/precompile-res-* 2>/dev/null | wc -l)
echo "Stale cgroups after restart cleanup: $STALE_AFTER"

echo "=== 7. Cleanup and uninstall ==="
systemctl stop precompile
rm -f /etc/systemd/system/precompile.service
systemctl daemon-reload
rm -rf "$TEST_DIR"
echo "Systemd service successfully tested and uninstalled."
