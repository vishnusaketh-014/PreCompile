import subprocess
import sys
import time
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.server import DashboardServer

def main():
    print("Starting DashboardServer on port 8787...")
    server = DashboardServer(host="127.0.0.1", port=8787)
    server.start(block=False)
    time.sleep(1.5)

    print("Running Playwright screenshot capture script...")
    env = os.environ.copy()
    env["NODE_PATH"] = os.path.abspath("dashboard/frontend/node_modules")

    node_cmd = ["node", "scripts/capture_real_screenshots.js"]
    res = subprocess.run(node_cmd, env=env, capture_output=True, text=True)
    print("STDOUT:")
    print(res.stdout)
    if res.stderr:
        print("STDERR:")
        print(res.stderr)

    print("Stopping DashboardServer...")
    server.stop()
    sys.exit(res.returncode)

if __name__ == "__main__":
    main()
