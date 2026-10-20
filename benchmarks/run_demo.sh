#!/usr/bin/env bash
set -e

pkill -f 'precompile daemon' || true
rm -f ~/.local/share/precompile/precompile.db* /tmp/daemon.pid /tmp/precompile_daemon.log

python3 -m precompile projects add --id demo --path ~/precompile-demo --tools default
(cd ~/precompile-demo && make clean)

echo "Starting daemon..."
python3 -u -m precompile daemon --pid-file /tmp/daemon.pid > /tmp/precompile_daemon.log 2>&1 &
DAEMON_PID=$!
sleep 2

echo "Running 14 cycles of build_on_save..."
python3 benchmarks/build_on_save.py --project ~/precompile-demo --cycles 14 --delay-mean 1.8 --delay-jitter 0.1

sleep 1
echo "Stopping daemon..."
kill -TERM "$DAEMON_PID" 2>/dev/null || pkill -f 'precompile daemon' || true
sleep 1

echo ""
echo "=== DAEMON LOGS ==="
cat /tmp/precompile_daemon.log

echo ""
echo "=== PRECOMPILE STATUS ==="
python3 -m precompile status

echo ""
echo "=== PRECOMPILE PREDICT ==="
python3 -m precompile predict --project demo
