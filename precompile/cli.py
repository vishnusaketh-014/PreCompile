"""PreCompile command-line interface entry point.

Provides a unified CLI with subcommands: daemon, status, projects, predict.
Maps user inputs to underlying stages (Stages 1 through 10).

SIGHUP Reload:
    After any `projects add/remove/enable/disable`, if the daemon PID file exists
    and the daemon process is alive, the CLI sends SIGHUP to trigger a live project
    list reload in the running daemon.
"""

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from precompile.config import get_default_db_path, validate_store_path
from precompile.daemon import PreCompileDaemon
from precompile.matcher import resolve_tool_profile
from precompile.predictor import LatencyPredictor
from precompile.store import DataStore

logger = logging.getLogger("precompile.cli")

# Default PID file location (same as daemon default, can be overridden by --pid-file)
DEFAULT_PID_FILE = Path("/tmp/precompile-daemon.pid")


def _get_store(db_path_arg: Optional[str]) -> DataStore:
    """Helper to instantiate DataStore from CLI argument or default path."""
    if db_path_arg:
        return DataStore(db_path=Path(db_path_arg))
    return DataStore()


def _is_mnt_path(path_str: str) -> bool:
    """Check if resolved path resides under /mnt/."""
    try:
        resolved = str(Path(path_str).resolve())
        return resolved.startswith("/mnt/")
    except Exception:
        return False


def _resolve_project_path(path_str: str) -> str:
    """Resolve a project path to an absolute, user-expanded path.

    Expands ~ and ~user, then makes absolute relative to cwd.

    Args:
        path_str: Raw path string from user input.

    Returns:
        Absolute path string (does NOT resolve symlinks).
    """
    expanded = os.path.expanduser(path_str)
    return str(Path(expanded).absolute())


def _notify_daemon_reload(pid_file: Optional[Path] = None) -> bool:
    """Send SIGHUP to the running daemon so it reloads its project list.

    Args:
        pid_file: Path to the daemon PID file. Falls back to DEFAULT_PID_FILE.

    Returns:
        True if SIGHUP was sent successfully, False otherwise.
    """
    pid_path = pid_file if pid_file is not None else DEFAULT_PID_FILE
    if not pid_path.exists():
        return False
    try:
        pid_text = pid_path.read_text(encoding="utf-8").strip()
        pid = int(pid_text)
    except (OSError, ValueError):
        return False

    # Check process is alive
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False

    try:
        os.kill(pid, signal.SIGHUP)
        return True
    except (OSError, AttributeError):
        return False


def build_parser() -> argparse.ArgumentParser:
    """Construct the command-line argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="precompile",
        description="PreCompile: Anticipatory Priority Reservation for Software Builds",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # 1. projects
    p_projects = subparsers.add_parser("projects", help="Manage registered projects")
    proj_sub = p_projects.add_subparsers(dest="projects_action", help="Project action")

    # projects add
    p_add = proj_sub.add_parser("add", help="Add a new project to monitor")
    p_add.add_argument("--id", required=True, help="Unique project identifier")
    p_add.add_argument("--path", required=True, help="Root directory path of project")
    p_add.add_argument(
        "--tools",
        default="default",
        help="Tool profile name ('default' or 'custom:gcc,make,ninja')",
    )
    p_add.add_argument("--db", default=None, help="Path to database file")
    p_add.add_argument("--pid-file", default=None, help="Daemon PID file to notify on change")

    # projects list
    p_list = proj_sub.add_parser("list", help="List registered projects")
    p_list.add_argument("--db", default=None, help="Path to database file")

    # projects remove
    p_remove = proj_sub.add_parser("remove", help="Remove a registered project")
    p_remove.add_argument("--id", required=True, help="Project identifier to delete")
    p_remove.add_argument("--db", default=None, help="Path to database file")
    p_remove.add_argument("--pid-file", default=None, help="Daemon PID file to notify on change")

    # projects enable
    p_enable = proj_sub.add_parser("enable", help="Enable monitoring for a project")
    p_enable.add_argument("--id", required=True, help="Project identifier")
    p_enable.add_argument("--db", default=None, help="Path to database file")
    p_enable.add_argument("--pid-file", default=None, help="Daemon PID file to notify on change")

    # projects disable
    p_disable = proj_sub.add_parser("disable", help="Disable monitoring for a project")
    p_disable.add_argument("--id", required=True, help="Project identifier")
    p_disable.add_argument("--db", default=None, help="Path to database file")
    p_disable.add_argument("--pid-file", default=None, help="Daemon PID file to notify on change")

    # 2. daemon
    p_daemon = subparsers.add_parser("daemon", help="Run the background monitoring daemon")
    p_daemon.add_argument(
        "--observe-only",
        action="store_true",
        default=False,
        help="Run strictly in observation-only mode (default)",
    )
    p_daemon.add_argument(
        "--actuate",
        action="store_true",
        default=False,
        help="Enable priority reservation actuation via cgroups (requires root)",
    )
    p_daemon.add_argument(
        "--holdout-prob",
        type=float,
        default=0.1,
        help="Probability of selecting holdout when arming (default: 0.1)",
    )
    p_daemon.add_argument(
        "--weight",
        type=int,
        default=500,
        help="Elevated cgroup CPU weight for reservations (default: 500, clamped to cap)",
    )
    p_daemon.add_argument(
        "--no-sweeper",
        action="store_true",
        default=False,
        help="Disable the post-claim descendant fixpoint sweeper (sweep_window_s=0)",
    )
    p_daemon.add_argument(
        "--no-contention-gate",
        action="store_true",
        default=False,
        help="Disable the PSI CPU contention gate check",
    )
    p_daemon.add_argument(
        "--no-cost-gate",
        action="store_true",
        default=False,
        help="Disable the cost/benefit gate check (arm whenever confidence gate passes)",
    )
    p_daemon.add_argument(
        "--eval-every",
        type=int,
        default=10,
        help="Cadence of benefit re-evaluation in number of builds (default: 10)",
    )
    p_daemon.add_argument(
        "--min-total-n",
        type=int,
        default=8,
        help="Minimum builds per arm required before evaluating benefit (default: 8)",
    )
    p_daemon.add_argument(
        "--min-bin-n",
        type=int,
        default=5,
        help="Minimum builds per arm in a contention bin required to evaluate bin effect (default: 5)",
    )
    p_daemon.add_argument(
        "--leak-check",
        action="store_true",
        default=False,
        help="Enable background leak-checker thread instrumentation (+0.3s, +1.0s)",
    )
    p_daemon.add_argument(
        "--condition",
        choices=["c1-reactive", "c2-full", "c3-static"],
        default="c2-full",
        help="Baseline experimental condition: c1-reactive, c2-full (default), c3-static",
    )
    p_daemon.add_argument(
        "--ablation-reactive",
        action="store_true",
        default=False,
        help="Ablation A1: Alias for --condition c1-reactive",
    )
    p_daemon.add_argument(
        "--ablation-no-confidence-gate",
        action="store_true",
        default=False,
        help="Ablation A2: Arm even if can_predict=False (default fixed window if n<2)",
    )
    p_daemon.add_argument(
        "--ablation-no-contention-gate",
        action="store_true",
        default=False,
        help="Ablation A3: Arm regardless of system PSI CPU contention",
    )
    p_daemon.add_argument(
        "--ablation-fixed-window",
        type=str,
        default=None,
        help="Ablation A4: Use fixed lead/lag window LO,HI (e.g. '0.0,5.0') instead of learned distribution",
    )
    p_daemon.add_argument(
        "--ablation-no-holdout",
        action="store_true",
        default=False,
        help="Ablation A5: 100%% reservation rate; disable holdouts and benefit controller feedback",
    )
    p_daemon.add_argument(
        "--ablation-no-atomic-claim",
        action="store_true",
        default=False,
        help="Ablation Negative Control: Disable atomic claim locking in ReservationManager",
    )
    p_daemon.add_argument(
        "--boost-budget-seconds",
        type=float,
        default=900.0,
        help="Rolling elevated boost budget in seconds (default: 900.0)",
    )
    p_daemon.add_argument(
        "--budget-interval-s",
        type=float,
        default=3600.0,
        help="Rolling budget window in seconds (default: 3600.0)",
    )
    p_daemon.add_argument(
        "--max-cgroup-weight",
        type=int,
        default=1000,
        help="Maximum CFS cgroup weight ceiling (default: 1000, capped at 1000)",
    )
    p_daemon.add_argument("--db", default=None, help="Path to database file")
    p_daemon.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    p_daemon.add_argument("--pid-file", default=None, help="Path to write daemon PID file")

    # 3. status
    p_status = subparsers.add_parser("status", help="Show system status and model metrics")
    p_status.add_argument("--json", action="store_true", help="Format output as JSON")
    p_status.add_argument("--db", default=None, help="Path to database file")

    # 4. predict
    p_predict = subparsers.add_parser(
        "predict", help="Query prediction window and confidence gate for a project"
    )
    p_predict.add_argument(
        "--project", "-p", required=True, help="Project identifier to inspect"
    )
    p_predict.add_argument(
        "--file-class",
        default="source",
        choices=["source", "header", "other"],
        help="File class (default: source)",
    )
    p_predict.add_argument("--json", action="store_true", help="Format output as JSON")
    p_predict.add_argument("--db", default=None, help="Path to database file")

    # 5. reservations
    p_res = subparsers.add_parser("reservations", help="Show reservation history and summaries")
    p_res.add_argument("--project", "-p", default=None, help="Filter by project identifier")
    p_res.add_argument("--json", action="store_true", help="Format output as JSON")
    p_res.add_argument("--db", default=None, help="Path to database file")

    # 6. benefit (Stage 9)
    p_benefit = subparsers.add_parser("benefit", help="Show benefit estimation state and history")
    p_benefit.add_argument("--project", "-p", default=None, help="Filter by project identifier")
    p_benefit.add_argument("--json", action="store_true", help="Format output as JSON")
    p_benefit.add_argument("--db", default=None, help="Path to database file")

    # 7. cleanup
    p_clean = subparsers.add_parser("cleanup", help="Clean up stale cgroup reservations (requires root)")
    p_clean.add_argument("--cgroup-path", default="/sys/fs/cgroup", help="Cgroup root path")

    # 8. pause (Stage 10 kill switch)
    p_pause = subparsers.add_parser("pause", help="Engage kill switch to pause reservation actuation")
    p_pause.add_argument("--pause-file", default=None, help="Path to pause flag file")

    # 9. resume (Stage 10 kill switch)
    p_resume = subparsers.add_parser("resume", help="Disengage kill switch to resume reservation actuation")
    p_resume.add_argument("--pause-file", default=None, help="Path to pause flag file")

    # 10. dashboard (Web telemetric monitoring)
    p_dash = subparsers.add_parser("dashboard", help="Start the read-only web monitoring dashboard")
    p_dash.add_argument("--port", type=int, default=8787, help="Port to listen on (default: 8787)")
    p_dash.add_argument("--host", default="0.0.0.0", help="Host address to bind to (default: 0.0.0.0)")
    p_dash.add_argument("--db", default=None, help="Path to database file")
    p_dash.add_argument("--results-dir", default=None, help="Path to benchmark results directory")

    return parser


def _maybe_notify_daemon(args: argparse.Namespace) -> None:
    """Send SIGHUP to the daemon if its PID file exists and process is alive.

    Prints a message to stdout if SIGHUP was sent.
    """
    pid_file_arg = getattr(args, "pid_file", None)
    pid_file = Path(pid_file_arg) if pid_file_arg else None
    if _notify_daemon_reload(pid_file):
        pid_file_path = pid_file if pid_file else DEFAULT_PID_FILE
        pid_text = pid_file_path.read_text(encoding="utf-8").strip()
        print(f"Sent SIGHUP to daemon (pid {pid_text}); project list will reload.")


def cmd_projects(args: argparse.Namespace) -> int:
    """Manage registered projects."""
    if not args.projects_action:
        print("Error: Must specify an action for projects: add, list, remove, enable, disable")
        return 1

    with _get_store(getattr(args, "db", None)) as store:
        action = args.projects_action

        if action == "add":
            # Part A fix #1: resolve to absolute, user-expanded path before storing
            resolved_path = _resolve_project_path(args.path)

            if _is_mnt_path(resolved_path):
                print(
                    f"Error: Refusing project path '{resolved_path}' under /mnt/. "
                    "Linux inotify is unreliable on Windows DrvFs mount boundaries; "
                    "use a native Linux directory (e.g. ~/project or /tmp) instead."
                )
                return 1

            try:
                store.add_project(
                    project_id=args.id,
                    root_path=resolved_path,
                    tool_profile_name=args.tools,
                    enabled=True,
                )
                print(f"Registered project '{args.id}' at '{resolved_path}' (tools: {args.tools}).")
                _maybe_notify_daemon(args)
                return 0
            except ValueError as e:
                print(f"Error: {e}")
                return 1

        elif action == "list":
            projects = store.list_projects()
            if not projects:
                print("No projects registered.")
                return 0
            print(f"{'PROJECT ID':<20} {'ENABLED':<10} {'TOOLS':<20} {'ROOT PATH'}")
            print("-" * 80)
            for p in projects:
                status_str = "yes" if p.enabled else "no"
                print(f"{p.id:<20} {status_str:<10} {p.tool_profile_name:<20} {p.root_path}")
            return 0

        elif action == "remove":
            if store.remove_project(args.id):
                print(f"Removed project '{args.id}' and its history.")
                _maybe_notify_daemon(args)
                return 0
            else:
                print(f"Error: Project '{args.id}' not found.")
                return 1

        elif action == "enable":
            if store.set_project_enabled(args.id, True):
                print(f"Enabled project '{args.id}'.")
                _maybe_notify_daemon(args)
                return 0
            else:
                print(f"Error: Project '{args.id}' not found.")
                return 1

        elif action == "disable":
            if store.set_project_enabled(args.id, False):
                print(f"Disabled project '{args.id}'.")
                _maybe_notify_daemon(args)
                return 0
            else:
                print(f"Error: Project '{args.id}' not found.")
                return 1

    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """Run the PreCompile background daemon."""
    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )

    # Condition & Ablations
    condition = "c1-reactive" if bool(getattr(args, "ablation_reactive", False)) else getattr(args, "condition", "c2-full")
    ablation_no_conf = bool(getattr(args, "ablation_no_confidence_gate", False))
    ablation_no_cont = bool(getattr(args, "ablation_no_contention_gate", False)) or bool(getattr(args, "no_contention_gate", False))
    ablation_no_hold = bool(getattr(args, "ablation_no_holdout", False))
    ablation_no_atomic = bool(getattr(args, "ablation_no_atomic_claim", False))
    fixed_win_str = getattr(args, "ablation_fixed_window", None)

    fixed_win = None
    if fixed_win_str:
        try:
            parts = [float(x.strip()) for x in fixed_win_str.split(",")]
            if len(parts) == 2:
                fixed_win = (parts[0], parts[1])
            else:
                print("Error: --ablation-fixed-window must be two comma-separated floats 'LO,HI'", file=sys.stderr)
                return 1
        except Exception:
            print("Error: --ablation-fixed-window must be two comma-separated floats 'LO,HI'", file=sys.stderr)
            return 1

    # Validate condition & ablation compatibility
    if condition == "c1-reactive":
        if ablation_no_conf or fixed_win is not None or ablation_no_hold:
            print("Error: Ablations A2, A4, A5 cannot be combined with --condition c1-reactive.", file=sys.stderr)
            return 1
    elif condition == "c3-static":
        if ablation_no_conf or ablation_no_cont or fixed_win is not None or ablation_no_hold:
            print("Error: Ablations cannot be combined with --condition c3-static.", file=sys.stderr)
            return 1

    actuate = bool(getattr(args, "actuate", False)) and not bool(getattr(args, "observe_only", False))
    holdout_prob = float(getattr(args, "holdout_prob", 0.1))
    weight = int(getattr(args, "weight", 500))
    sweep_win = 0.0 if bool(getattr(args, "no_sweeper", False)) else None
    no_cost = bool(getattr(args, "no_cost_gate", False))
    eval_every = getattr(args, "eval_every", 10)
    min_total_n = getattr(args, "min_total_n", 8)
    min_bin_n = getattr(args, "min_bin_n", 5)
    leak_check = bool(getattr(args, "leak_check", False))
    boost_budget_seconds = getattr(args, "boost_budget_seconds", 900.0)
    budget_interval_s = getattr(args, "budget_interval_s", 3600.0)
    max_cgroup_weight = getattr(args, "max_cgroup_weight", 1000)

    daemon = PreCompileDaemon(
        db_path=args.db,
        pid_file=args.pid_file,
        actuate=actuate,
        holdout_probability=holdout_prob,
        reservation_weight=weight,
        sweep_window_s=sweep_win,
        contention_gate_enabled=not ablation_no_cont,
        cost_gate_enabled=not no_cost,
        eval_every=eval_every,
        min_total_n=min_total_n,
        min_bin_n=min_bin_n,
        leak_check=leak_check,
        condition=condition,
        ablation_no_confidence_gate=ablation_no_conf,
        ablation_no_contention_gate=ablation_no_cont,
        ablation_fixed_window=fixed_win,
        ablation_no_holdout=ablation_no_hold,
        ablation_no_atomic_claim=ablation_no_atomic,
        boost_budget_seconds=boost_budget_seconds,
        budget_interval_s=budget_interval_s,
        max_cgroup_weight=max_cgroup_weight,
    )
    daemon.run_forever()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print current daemon status, learned EWMA distributions, metrics, and decision counts."""
    from precompile.safety import SafetyGovernor
    gov = SafetyGovernor()

    with _get_store(getattr(args, "db", None)) as store:
        projects = store.list_projects()
        pred = LatencyPredictor(store=store)

        # Check latest daemon execution session
        latest_runs = store.list_daemon_runs(limit=1)
        latest_run = latest_runs[0] if latest_runs else None
        latest_run_data = None
        latest_flags = {}
        latest_condition = "c2-full"
        if latest_run is not None:
            if isinstance(latest_run.get("flags_json"), str):
                try:
                    latest_flags = json.loads(latest_run["flags_json"])
                except Exception:
                    latest_flags = {}
            elif isinstance(latest_run.get("flags_json"), dict):
                latest_flags = latest_run["flags_json"]
            latest_condition = latest_run.get("condition", "c2-full")
            latest_run_data = {
                "started_at": latest_run.get("started_at"),
                "stopped_at": latest_run.get("stopped_at"),
                "condition": latest_condition,
                "flags": latest_flags,
            }

        # Determine system operational mode
        if gov.is_paused():
            current_mode = "paused"
        elif gov.is_circuit_breaker_active():
            current_mode = "circuit-breaker cooldown"
        elif not gov.check_privileges():
            current_mode = f"observation-only (reason: {gov.last_degradation_reason})"
        else:
            if latest_run and latest_flags.get("actuate", False):
                current_mode = "actuating"
            elif latest_run and not latest_flags.get("actuate", False):
                current_mode = "observation-only (actuate=False)"
            else:
                current_mode = "observation-only"

        status_data: Dict[str, Any] = {
            "mode": current_mode,
            "latest_run": latest_run_data,
            "database": str(store.db_path),
            "projects_count": len(projects),
            "projects": [],
        }

        for p in projects:
            p_data: Dict[str, Any] = {
                "id": p.id,
                "root_path": p.root_path,
                "tool_profile": p.tool_profile_name,
                "enabled": p.enabled,
                "models": {},
                "observed_builds": len(store.builds_by_condition(p.id, "observed")),
                "decision_counts": store.decision_counts(p.id),
            }

            for fc in ["source", "header"]:
                key = (p.id, fc, p.tool_profile_name)
                desc = pred.describe(key)
                if desc["n"] > 0:
                    p_data["models"][fc] = desc

            status_data["projects"].append(p_data)

        if args.json:
            print(json.dumps(status_data, indent=2))
            return 0

        # Human-readable plain text output
        print("=== PreCompile System Status ===")
        print(f"Mode:       {status_data['mode']}")
        print(f"Database:   {status_data['database']}")
        print(f"Projects:   {status_data['projects_count']}")
        if latest_run_data:
            summary_flags = {k: v for k, v in latest_flags.items() if k in ("actuate", "condition", "holdout_probability", "reservation_weight")}
            flags_str = ", ".join(f"{k}={v}" for k, v in sorted(summary_flags.items()))
            print(f"Latest Run: condition={latest_condition} [{flags_str}]")
        print()

        for p in status_data["projects"]:
            status_str = "enabled" if p["enabled"] else "disabled"
            print(f"Project: {p['id']} [{status_str}]")
            print(f"  Path:            {p['root_path']}")
            print(f"  Tools:           {p['tool_profile']}")
            print(f"  Observed Builds: {p['observed_builds']}")

            # Decision counts (Step 7)
            dc = p.get("decision_counts", {})
            total_decisions = sum(dc.values())
            if total_decisions > 0:
                print(f"  Arming Decisions ({total_decisions} total):")
                for decision_key in [
                    "would_arm", "armed", "holdout", "abstain_confidence",
                    "abstain_no_contention", "abstain_cost", "abstain_no_build_estimate"
                ]:
                    cnt = dc.get(decision_key, 0)
                    if cnt > 0:
                        print(f"    {decision_key}: {cnt}")

            # Reservations summary (Step 8)
            res_summary = store.reservation_summary(p["id"])
            p_data["reservations"] = res_summary
            if res_summary["total"] > 0:
                counts_str = ", ".join(f"{st}: {cnt}" for st, cnt in sorted(res_summary["counts"].items()))
                lat_str = f", mean latency={res_summary['mean_claim_latency_s']:.3f}s" if res_summary["mean_claim_latency_s"] is not None else ""
                print(f"  Reservations ({res_summary['total']} total): {counts_str}{lat_str}")

            # Benefit summary (Step 9)
            b_state = store.get_benefit_state(p["id"])
            p_data["benefit_state"] = b_state
            if b_state is not None:
                eff_str = f"{b_state['last_effect']*100:.1f}%" if b_state["last_effect"] is not None else "N/A"
                p_str = f"{b_state['last_p_value']:.4f}" if b_state["last_p_value"] is not None else "N/A"
                print(
                    f"  Benefit:         status={b_state['status']} weight={b_state['weight']} "
                    f"arming={'yes' if b_state['arming_enabled'] else 'no'} effect={eff_str} (p={p_str})"
                )

            if not p["models"]:
                print("  Models:          (no delay samples recorded yet)")
            else:
                for fc, m in p["models"].items():
                    cv_str = f"{m['cv']:.3f}" if m['cv'] is not None else "N/A"
                    print(f"  Model [{fc}]:")
                    print(f"    Samples (n):      {m['n']}")
                    print(f"    Mean / Sigma / CV: {m['mean']:.2f}s / {m['sigma']:.2f}s / {cv_str}")
                    print(f"    Window:           [{m['window'][0]:.2f}s, {m['window'][1]:.2f}s]")
                    print(
                        f"    Hit-Rate (last10): {m['recent_hit_rate']['rate'] * 100:.1f}% "
                        f"({m['recent_hit_rate']['hits']}/{m['recent_hit_rate']['scored_total']})"
                    )
                    gate = m["gate_result"]
                    print(f"    Can Predict:      {gate['can_predict']}")
                    print(f"    Can Arm:          {gate['can_arm']}")
                    if gate["reasons"]:
                        print(f"    Gate Reasons:     {'; '.join(gate['reasons'])}")
            print()

        return 0


def cmd_predict(args: argparse.Namespace) -> int:
    """Query prediction window, confidence gate, and decision counts for a specific project."""
    with _get_store(getattr(args, "db", None)) as store:
        proj = store.get_project(args.project)
        if proj is None:
            print(f"Error: Project '{args.project}' not found.")
            return 1

        pred = LatencyPredictor(store=store)
        key = (proj.id, args.file_class, proj.tool_profile_name)
        desc = pred.describe(key)

        # Add decision counts to the describe output
        decision_counts = store.decision_counts(proj.id)
        desc["decision_counts"] = decision_counts

        if args.json:
            print(json.dumps(desc, indent=2))
            return 0

        cv_str = f"{desc['cv']:.3f}" if desc['cv'] is not None else "N/A"
        print(f"Prediction details for project '{args.project}' [{args.file_class}]:")
        print(f"  Samples (n):      {desc['n']}")
        print(f"  Mean Delay:       {desc['mean']:.3f}s")
        print(f"  Std Dev (Sigma):  {desc['sigma']:.3f}s")
        print(f"  CV:               {cv_str}")
        print(f"  Window:           [{desc['window'][0]:.2f}s, {desc['window'][1]:.2f}s]")
        hr = desc["recent_hit_rate"]
        print(f"  Hit Rate:         {hr['rate'] * 100:.1f}% ({hr['hits']}/{hr['scored_total']} scored)")
        gate = desc["gate_result"]
        print(f"  Can Predict:      {gate['can_predict']}")
        print(f"  Can Arm:          {gate['can_arm']}")
        if gate["reasons"]:
            print(f"  Reasons:          {'; '.join(gate['reasons'])}")

        # Decision counts
        total_decisions = sum(decision_counts.values())
        if total_decisions > 0:
            print(f"  Arming Decisions ({total_decisions} total):")
            for dk in [
                "would_arm", "holdout", "abstain_confidence",
                "abstain_no_contention", "abstain_cost", "abstain_no_build_estimate"
            ]:
                cnt = decision_counts.get(dk, 0)
                if cnt > 0:
                    print(f"    {dk}: {cnt}")

        return 0


def cmd_reservations(args: argparse.Namespace) -> int:
    """Show reservation history and summary metrics."""
    with _get_store(getattr(args, "db", None)) as store:
        summary = store.reservation_summary(args.project)
        reservations = store.list_reservations(args.project)

        if args.json:
            print(json.dumps({"summary": summary, "reservations": reservations}, indent=2))
            return 0

        title = f"Reservations for project '{args.project}'" if args.project else "PreCompile Reservations"
        print(f"=== {title} ===")
        print(f"Total: {summary['total']}")
        if summary["counts"]:
            for st, cnt in sorted(summary["counts"].items()):
                print(f"  {st}: {cnt}")
        if summary["mean_claim_latency_s"] is not None:
            print(f"Mean Claim Latency: {summary['mean_claim_latency_s']:.3f}s")
        else:
            print("Mean Claim Latency: N/A")

        if reservations:
            print("\nRecent Reservations:")
            for r in reservations[:10]:
                lat_str = f" lat={r['claim_latency_s']:.3f}s" if r.get("claim_latency_s") is not None else ""
                pid_str = f" pid={r['claimed_pid']}" if r.get("claimed_pid") else ""
                print(f"  [{r['final_state']}] {r['id']} ({r['project_id']}) weight={r['weight']}{pid_str}{lat_str}")
        return 0


def cmd_benefit(args: argparse.Namespace) -> int:
    """Show benefit estimation state and history."""
    with _get_store(getattr(args, "db", None)) as store:
        projects = [store.get_project(args.project)] if args.project else store.list_projects()
        projects = [p for p in projects if p is not None]

        results = []
        for p in projects:
            st = store.get_benefit_state(p.id)
            evals = store.list_benefit_evals(p.id, limit=10)
            results.append({"project_id": p.id, "state": st, "evaluations": evals})

        if args.json:
            print(json.dumps(results, indent=2))
            return 0

        title = f"Benefit Estimation for project '{args.project}'" if args.project else "Benefit Estimation Status"
        print(f"=== {title} ===")
        for item in results:
            pid = item["project_id"]
            st = item["state"]
            print(f"\nProject: {pid}")
            if st is None:
                print("  State: No benefit evaluation performed yet.")
            else:
                eff = f"{st['last_effect']*100:.1f}%" if st['last_effect'] is not None else "N/A"
                p_v = f"{st['last_p_value']:.4f}" if st['last_p_value'] is not None else "N/A"
                print(f"  Status:         {st['status']}")
                print(f"  Current Weight: {st['weight']}")
                print(f"  Arming Enabled: {st['arming_enabled']}")
                print(f"  Last Speedup:   {eff} (p={p_v})")
                print(f"  Samples:        {st['n_reserved']} reserved / {st['n_holdout']} holdout")

            evals = item["evaluations"]
            if evals:
                print("  Recent Evaluations:")
                for ev in evals[:5]:
                    e_eff = f"{ev['overall_effect']*100:+.1f}%"
                    ci = f"[{ev['ci_low']*100:+.1f}%, {ev['ci_high']*100:+.1f}%]"
                    print(
                        f"    action={ev['action']:<7} decision_effect={e_eff:<7} 95%CI={ci:<18} "
                        f"p={ev['p_value']:.4f} weight={ev['weight_after']} arming={ev['arming_after']}"
                    )
                    try:
                        pb = json.loads(ev.get("per_bin_json", "{}"))
                        for b_name in ["low", "mild", "moderate", "high"]:
                            if b_name in pb and pb[b_name].get("effect") is not None:
                                b_info = pb[b_name]
                                b_eff = f"{b_info['effect']*100:+.1f}%"
                                b_ci = f"[{b_info['ci_low']*100:+.1f}%, {b_info['ci_high']*100:+.1f}%]" if b_info.get('ci_low') is not None else "N/A"
                                print(f"      - {b_name:<8} (nR={b_info['n_reserved']}, nH={b_info['n_holdout']}): eff={b_eff:<7} 95%CI={b_ci:<18} p={b_info['p_value']:.4f}")
                        if "pooled" in pb and pb["pooled"].get("effect") is not None:
                            p_info = pb["pooled"]
                            p_eff = f"{p_info['effect']*100:+.1f}%"
                            p_ci = f"[{p_info['ci_low']*100:+.1f}%, {p_info['ci_high']*100:+.1f}%]"
                            print(f"      - [pooled] : eff={p_eff:<7} 95%CI={p_ci:<18} p={p_info['p_value']:.4f}")
                    except Exception:
                        pass
        return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    """Clean up stale cgroup reservations."""
    from precompile.cgroup import CgroupController, CgroupError
    cgroup_path = getattr(args, "cgroup_path", "/sys/fs/cgroup")
    controller = CgroupController(base_cgroup_path=cgroup_path)
    try:
        cleaned = controller.cleanup_stale()
        print(f"Cleaned up {cleaned} stale cgroup reservation(s).")
        return 0
    except CgroupError as e:
        print(f"Error cleaning up cgroups: {e}")
        return 1


def cmd_pause(args: argparse.Namespace) -> int:
    """Engage kill switch to pause actuation."""
    from precompile.safety import SafetyGovernor
    pause_file = Path(args.pause_file) if getattr(args, "pause_file", None) else None
    gov = SafetyGovernor(pause_file=pause_file)
    created = gov.pause()
    print(f"PreCompile paused: kill switch flag file created at {created}.")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """Disengage kill switch to resume actuation."""
    from precompile.safety import SafetyGovernor
    pause_file = Path(args.pause_file) if getattr(args, "pause_file", None) else None
    gov = SafetyGovernor(pause_file=pause_file)
    removed = gov.resume()
    if removed:
        print("PreCompile resumed: kill switch disengaged.")
    else:
        print("PreCompile was not paused.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entry point."""
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        parser.print_help()
        return 0

    args = parser.parse_args(argv)

    if args.subcommand == "projects":
        return cmd_projects(args)
    elif args.subcommand == "daemon":
        return cmd_daemon(args)
    elif args.subcommand == "status":
        return cmd_status(args)
    elif args.subcommand == "predict":
        return cmd_predict(args)
    elif args.subcommand == "reservations":
        return cmd_reservations(args)
    elif args.subcommand == "benefit":
        return cmd_benefit(args)
    elif args.subcommand == "cleanup":
        return cmd_cleanup(args)
    elif args.subcommand == "pause":
        return cmd_pause(args)
    elif args.subcommand == "resume":
        return cmd_resume(args)
    elif args.subcommand == "dashboard":
        from precompile.dashboard import run_dashboard
        return run_dashboard(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
