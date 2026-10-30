"""PreCompile Dashboard package.

Provides a read-only, demo-ready web monitoring and visualization interface.
Strictly adheres to:
1. Read-only SQLite access (uri=file:...mode=ro) with zero actuation capability.
2. Zero build step, zero CDN dependencies, fully offline-capable.
3. Server-Sent Events (SSE) updates at 1 Hz for minimal host scheduling interference.
4. Absolute data honesty: every number is derived directly from SQLite or benchmark JSONL.
"""

from dashboard.reader import DashboardDataReader
from dashboard.server import DashboardServer, run_dashboard

__all__ = ["DashboardDataReader", "DashboardServer", "run_dashboard"]
