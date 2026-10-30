"""PreCompile Dashboard package alias for unified CLI integration."""

from dashboard.reader import DashboardDataReader
from dashboard.server import DashboardServer, run_dashboard

__all__ = ["DashboardDataReader", "DashboardServer", "run_dashboard"]
