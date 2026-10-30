import subprocess
import sys
import time
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dashboard.server import DashboardServer

def main():
    server = DashboardServer(host="127.0.0.1", port=8787)
    server.start(block=False)
    time.sleep(1.0)

    env = os.environ.copy()
    env["NODE_PATH"] = os.path.abspath("dashboard/frontend/node_modules")

    node_cmd = ["node", "scripts/verify_network_requests.js"]
    res = subprocess.run(node_cmd, env=env, capture_output=True, text=True)
    print(res.stdout)
    if res.stderr:
        print("STDERR:", res.stderr)

    server.stop()
    sys.exit(res.returncode)

if __name__ == "__main__":
    main()
