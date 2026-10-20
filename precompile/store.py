"""SQLite data persistence layer for projects, learned models, and metrics.

Stage Mapping: Stage 3 (Latency Learning), Stage 4 (Confidence Gate History),
Stage 8 (Lifecycle Outcomes & Misses), and Stage 9 (Holdout Metrics).

Concurrency & Thread-Safety Model:
    This store uses a single sqlite3.Connection with check_same_thread=False guarded
    by a reentrant lock (threading.RLock()). This ensures strict thread safety across
    the watcher thread, monitor thread, and daemon/cli main thread, eliminates SQLite
    busy/locking conflicts across threads, ensures atomic transactions, and allows
    identical execution semantics in tests using in-memory databases (':memory:').
"""

import csv
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from precompile.config import StoreConfig, get_default_db_path, validate_store_path

logger = logging.getLogger("precompile.store")


@dataclass
class ProjectRecord:
    """Registered project schema."""
    project_id: str
    root_path: str
    tool_profile_name: str = "default"
    enabled: bool = True
    created_at: float = field(default_factory=time.time)

    def __init__(
        self,
        project_id: str,
        root_path: str,
        tool_profile_name: Optional[str] = None,
        enabled: bool = True,
        created_at: Optional[float] = None,
        tool_profiles_json: Optional[str] = None,
    ) -> None:
        self.project_id = project_id
        self.root_path = str(root_path)
        self.tool_profile_name = tool_profile_name or tool_profiles_json or "default"
        self.enabled = bool(enabled)
        self.created_at = created_at if created_at is not None else time.time()

    @property
    def id(self) -> str:
        return self.project_id

    @property
    def tool_profiles_json(self) -> str:
        return self.tool_profile_name


@dataclass
class DelaySampleRecord:
    """Observed event-to-process latency sample."""
    id: Optional[int] = None
    project_id: str = ""
    file_class: str = ""
    tool_profile: str = ""
    delay_s: float = 0.0
    accepted: bool = True
    t_save_wall: float = 0.0
    created_at: float = 0.0

    def __init__(
        self,
        id: Optional[int] = None,
        project_id: str = "",
        file_class: str = "",
        tool_profile: Optional[str] = None,
        delay_s: Optional[float] = None,
        accepted: bool = True,
        t_save_wall: Optional[float] = None,
        created_at: Optional[float] = None,
        # Backward compatibility aliases
        sample_id: Optional[int] = None,
        tool_name: Optional[str] = None,
        save_timestamp: Optional[float] = None,
        process_timestamp: Optional[float] = None,
        delay_seconds: Optional[float] = None,
    ) -> None:
        self.id = id if id is not None else sample_id
        self.project_id = project_id
        self.file_class = file_class
        self.tool_profile = tool_profile or tool_name or "default"
        self.delay_s = (
            delay_s if delay_s is not None else (delay_seconds if delay_seconds is not None else 0.0)
        )
        self.accepted = bool(accepted)
        self.t_save_wall = (
            t_save_wall
            if t_save_wall is not None
            else (save_timestamp if save_timestamp is not None else time.time())
        )
        self.created_at = created_at if created_at is not None else time.time()

    @property
    def sample_id(self) -> Optional[int]:
        return self.id

    @property
    def tool_name(self) -> str:
        return self.tool_profile

    @property
    def delay_seconds(self) -> float:
        return self.delay_s

    @property
    def save_timestamp(self) -> float:
        return self.t_save_wall


@dataclass
class PredictionRecord:
    """Record of a prediction window and its real-world outcome."""
    id: Optional[int] = None
    project_id: str = ""
    file_class: str = ""
    tool_profile: str = ""
    t_save_wall: float = 0.0
    window_lo: float = 0.0
    window_hi: float = 0.0
    armed: bool = False
    holdout: bool = False
    outcome: Optional[str] = None  # 'hit', 'miss', or 'no_build'
    observed_delay_s: Optional[float] = None
    created_at: float = 0.0

    @property
    def prediction_id(self) -> Optional[int]:
        return self.id


PredictionOutcomeRecord = PredictionRecord


@dataclass
class BuildRecord:
    """Historical build record feeding the benefit estimator (Stage 9)."""
    id: Optional[int] = None
    project_id: str = ""
    condition: str = ""  # 'reserved', 'holdout', or 'observed'
    contention_level: float = 0.0  # CPU PSI avg10 at build start
    duration_s: float = 0.0
    t_start_wall: float = 0.0
    t_end_wall: float = 0.0
    prediction_id: Optional[int] = None
    created_at: float = 0.0


class DataStore:
    """SQLite-backed persistent data store for PreCompile."""

    def __init__(
        self,
        db_path: Union[str, Path, None] = None,
        config: Optional[StoreConfig] = None,
    ) -> None:
        """Initialize SQLite database connection and verify schema.

        Args:
            db_path: Path to database file, or ':memory:'. Defaults to config.db_path.
            config: StoreConfig instance.
        """
        self.config = config if config is not None else StoreConfig()
        if db_path is not None:
            self.db_path = db_path if db_path == ":memory:" else Path(db_path)
        else:
            self.db_path = self.config.db_path

        self._is_memory = (self.db_path == ":memory:")
        self._lock = threading.RLock()
        self._write_count = 0
        self._prune_interval = 50

        if not self._is_memory:
            validate_store_path(self.db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        else:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)

        # Pragmas
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            if not self._is_memory:
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("PRAGMA synchronous = NORMAL")

        self.migrate()

    def __enter__(self) -> "DataStore":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close SQLite database connection cleanly."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def migrate(self) -> None:
        """Idempotently apply database migrations and initialize schema."""
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_version (
                        version INTEGER PRIMARY KEY,
                        applied_at REAL NOT NULL
                    )
                    """
                )
                cur = self._conn.execute("SELECT MAX(version) FROM schema_version")
                row = cur.fetchone()
                current_version = row[0] if (row and row[0] is not None) else 0

                if current_version < 1:
                    self._apply_migration_v1()
                    self._conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (1, ?)",
                        (time.time(),),
                    )
                    current_version = 1

                if current_version < 2:
                    self._apply_migration_v2()
                    self._conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (2, ?)",
                        (time.time(),),
                    )
                    current_version = 2

                if current_version < 3:
                    self._apply_migration_v3()
                    self._conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (3, ?)",
                        (time.time(),),
                    )
                    current_version = 3

                if current_version < 4:
                    self._apply_migration_v4()
                    self._conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (4, ?)",
                        (time.time(),),
                    )
                    current_version = 4

                if current_version < 5:
                    self._apply_migration_v5()
                    self._conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (5, ?)",
                        (time.time(),),
                    )
                    current_version = 5

    def initialize_schema(self) -> None:
        """Backward-compatibility alias for migrate()."""
        self.migrate()

    def _apply_migration_v1(self) -> None:
        """Initial schema migration: projects, delay_samples, model_state, predictions, builds."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                root_path TEXT NOT NULL,
                tool_profile_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS delay_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_class TEXT NOT NULL,
                tool_profile TEXT NOT NULL,
                delay_s REAL NOT NULL,
                accepted INTEGER NOT NULL,
                t_save_wall REAL NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS model_state (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_class TEXT NOT NULL,
                tool_profile TEXT NOT NULL,
                n INTEGER NOT NULL,
                mean REAL NOT NULL,
                var REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (project_id, file_class, tool_profile)
            );

            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_class TEXT NOT NULL,
                tool_profile TEXT NOT NULL,
                t_save_wall REAL NOT NULL,
                window_lo REAL NOT NULL,
                window_hi REAL NOT NULL,
                armed INTEGER NOT NULL,
                holdout INTEGER NOT NULL,
                outcome TEXT,
                observed_delay_s REAL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS builds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                condition TEXT NOT NULL,
                contention_level REAL NOT NULL,
                duration_s REAL NOT NULL,
                t_start_wall REAL NOT NULL,
                t_end_wall REAL NOT NULL,
                prediction_id INTEGER REFERENCES predictions(id) ON DELETE SET NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_samples_key
                ON delay_samples(project_id, file_class, tool_profile, id);
            CREATE INDEX IF NOT EXISTS idx_predictions_key
                ON predictions(project_id, file_class, tool_profile, id);
            CREATE INDEX IF NOT EXISTS idx_builds_key
                ON builds(project_id, condition, id);
            """
        )

    def _apply_migration_v2(self) -> None:
        """Migration v2: add decision columns to predictions table.

        Adds:
            decision TEXT       -- 'would_arm', 'holdout', 'abstain_confidence',
                                   'abstain_no_contention', 'abstain_cost',
                                   'abstain_no_build_estimate'
            contention_level REAL -- PSI level at decision time
            benefit_s REAL        -- estimated benefit in seconds
            cost_s REAL           -- estimated cost in seconds

        Uses ALTER TABLE so existing v1 databases are upgraded with no data loss.
        Each ALTER TABLE is guarded to be idempotent (column may already exist).
        """
        new_columns = [
            ("decision", "TEXT"),
            ("contention_level", "REAL"),
            ("benefit_s", "REAL"),
            ("cost_s", "REAL"),
        ]
        # Check which columns already exist
        cur = self._conn.execute("PRAGMA table_info(predictions)")
        existing_cols = {row[1] for row in cur.fetchall()}
        for col_name, col_type in new_columns:
            if col_name not in existing_cols:
                self._conn.execute(
                    f"ALTER TABLE predictions ADD COLUMN {col_name} {col_type}"
                )

    def _apply_migration_v3(self) -> None:
        """Migration v3: create reservations table for tracking cgroup allocations."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reservations (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                prediction_id INTEGER REFERENCES predictions(id) ON DELETE SET NULL,
                final_state TEXT NOT NULL,
                weight INTEGER NOT NULL,
                armed_at REAL NOT NULL,
                window_lo_abs_s REAL NOT NULL,
                window_hi_abs_s REAL NOT NULL,
                claimed_at REAL,
                claim_latency_s REAL,
                claimed_pid INTEGER,
                closed_at REAL,
                group_name TEXT NOT NULL,
                original_cgroup TEXT,
                reason TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_reservations_project
                ON reservations(project_id, id);
            """
        )

    def _apply_migration_v4(self) -> None:
        """Migration v4: Create benefit_state and benefit_evals tables (Stage 9)."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS benefit_state (
                project_id TEXT PRIMARY KEY,
                weight INTEGER NOT NULL,
                arming_enabled INTEGER NOT NULL,
                status TEXT NOT NULL,
                last_effect REAL,
                last_p_value REAL,
                n_reserved INTEGER NOT NULL DEFAULT 0,
                n_holdout INTEGER NOT NULL DEFAULT 0,
                last_eval_at REAL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS benefit_evals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                evaluated_at REAL NOT NULL,
                n_reserved INTEGER NOT NULL,
                n_holdout INTEGER NOT NULL,
                per_bin_json TEXT NOT NULL,
                overall_effect REAL NOT NULL,
                ci_low REAL NOT NULL,
                ci_high REAL NOT NULL,
                p_value REAL NOT NULL,
                action TEXT NOT NULL,
                weight_after INTEGER NOT NULL,
                arming_after INTEGER NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_benefit_evals_project
                ON benefit_evals(project_id, evaluated_at);
            """
        )

    def _apply_migration_v5(self) -> None:
        """Migration v5: Create daemon_runs table for condition and ablation auditing (Stage 10)."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS daemon_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at REAL NOT NULL,
                condition TEXT NOT NULL,
                flags_json TEXT NOT NULL,
                code_version TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_daemon_runs_started
                ON daemon_runs(started_at);
            """
        )


    # --- Project Management ---

    def add_project(
        self,
        project_id: Union[str, ProjectRecord],
        root_path: Optional[Union[str, Path]] = None,
        tool_profile_name: str = "default",
        enabled: bool = True,
    ) -> ProjectRecord:
        """Insert a registered project. Raises ValueError if ID already exists."""
        if isinstance(project_id, ProjectRecord):
            record = project_id
        else:
            if root_path is None:
                raise ValueError("root_path must be provided when adding project by string ID")
            record = ProjectRecord(
                project_id=project_id,
                root_path=str(root_path),
                tool_profile_name=tool_profile_name,
                enabled=enabled,
                created_at=time.time(),
            )

        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        """
                        INSERT INTO projects (id, root_path, tool_profile_name, enabled, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            record.project_id,
                            record.root_path,
                            record.tool_profile_name,
                            1 if record.enabled else 0,
                            record.created_at,
                        ),
                    )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Project with ID '{record.project_id}' already exists") from e

        return record

    def get_project(self, project_id: str) -> Optional[ProjectRecord]:
        """Fetch project by ID, returning ProjectRecord or None."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, root_path, tool_profile_name, enabled, created_at FROM projects WHERE id = ?",
                (project_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return ProjectRecord(
                project_id=row[0],
                root_path=row[1],
                tool_profile_name=row[2],
                enabled=bool(row[3]),
                created_at=row[4],
            )

    def list_projects(self) -> List[ProjectRecord]:
        """List all registered projects."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, root_path, tool_profile_name, enabled, created_at FROM projects ORDER BY id ASC"
            )
            rows = cur.fetchall()
            return [
                ProjectRecord(
                    project_id=r[0],
                    root_path=r[1],
                    tool_profile_name=r[2],
                    enabled=bool(r[3]),
                    created_at=r[4],
                )
                for r in rows
            ]

    def set_project_enabled(self, project_id: str, enabled: bool) -> bool:
        """Toggle enabled status of a project. Returns True if project exists."""
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE projects SET enabled = ? WHERE id = ?",
                    (1 if enabled else 0, project_id),
                )
                return cur.rowcount > 0

    def remove_project(self, project_id: str) -> bool:
        """Remove a project and cascade-delete its dependent rows. Returns True if deleted."""
        with self._lock:
            with self._conn:
                cur = self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
                return cur.rowcount > 0

    # --- Delay Samples ---

    def record_sample(
        self,
        project_id: str,
        file_class: str,
        tool_profile: str,
        delay_s: float,
        accepted: bool = True,
        t_save_wall: Optional[float] = None,
    ) -> int:
        """Persist an observed save-to-process delay sample."""
        now = time.time()
        t_save = t_save_wall if t_save_wall is not None else now
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO delay_samples
                        (project_id, file_class, tool_profile, delay_s, accepted, t_save_wall, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        file_class,
                        tool_profile,
                        float(delay_s),
                        1 if accepted else 0,
                        float(t_save),
                        now,
                    ),
                )
                sample_id = cur.lastrowid
                self._write_count += 1
                if self._write_count % self._prune_interval == 0:
                    self.prune()
                return sample_id

    def record_delay_sample(self, sample: DelaySampleRecord) -> int:
        """Backward-compatibility wrapper for record_sample."""
        return self.record_sample(
            project_id=sample.project_id,
            file_class=sample.file_class,
            tool_profile=sample.tool_profile,
            delay_s=sample.delay_s,
            accepted=sample.accepted,
            t_save_wall=sample.t_save_wall,
        )

    def recent_samples(
        self,
        project_id: str,
        file_class: str,
        tool_profile: str,
        n: int = 50,
        ascending: bool = True,
    ) -> List[DelaySampleRecord]:
        """Retrieve recent delay samples for model initialization.

        Returns up to n most recent samples, chronologically ordered if ascending=True.
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT id, project_id, file_class, tool_profile, delay_s, accepted, t_save_wall, created_at
                FROM delay_samples
                WHERE project_id = ? AND file_class = ? AND tool_profile = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (project_id, file_class, tool_profile, n),
            )
            rows = cur.fetchall()
            if ascending:
                rows.reverse()
            return [
                DelaySampleRecord(
                    id=r[0],
                    project_id=r[1],
                    file_class=r[2],
                    tool_profile=r[3],
                    delay_s=r[4],
                    accepted=bool(r[5]),
                    t_save_wall=r[6],
                    created_at=r[7],
                )
                for r in rows
            ]

    def get_recent_samples(
        self,
        project_id: str,
        file_class: str,
        tool_name: str,
        limit: int = 50,
    ) -> List[DelaySampleRecord]:
        """Backward-compatibility alias for recent_samples."""
        return self.recent_samples(
            project_id=project_id,
            file_class=file_class,
            tool_profile=tool_name,
            n=limit,
        )

    def sample_count(
        self,
        project_id: str,
        file_class: str,
        tool_profile: str,
        accepted_only: bool = True,
    ) -> int:
        """Count total samples recorded for a given (project_id, file_class, tool_profile)."""
        with self._lock:
            if accepted_only:
                cur = self._conn.execute(
                    """
                    SELECT COUNT(*) FROM delay_samples
                    WHERE project_id = ? AND file_class = ? AND tool_profile = ? AND accepted = 1
                    """,
                    (project_id, file_class, tool_profile),
                )
            else:
                cur = self._conn.execute(
                    """
                    SELECT COUNT(*) FROM delay_samples
                    WHERE project_id = ? AND file_class = ? AND tool_profile = ?
                    """,
                    (project_id, file_class, tool_profile),
                )
            return cur.fetchone()[0]

    # --- Model State ---

    def load_model_state(
        self,
        key: Union[Tuple[str, str, str], str],
        file_class: Optional[str] = None,
        tool_profile: Optional[str] = None,
    ) -> Optional[Tuple[int, float, float]]:
        """Load persisted EWMA state (n, mean, var) for a model key."""
        if isinstance(key, tuple):
            proj_id, fc, tp = key
        else:
            proj_id, fc, tp = key, file_class, tool_profile

        with self._lock:
            cur = self._conn.execute(
                """
                SELECT n, mean, var FROM model_state
                WHERE project_id = ? AND file_class = ? AND tool_profile = ?
                """,
                (proj_id, fc, tp),
            )
            row = cur.fetchone()
            if not row:
                return None
            return int(row[0]), float(row[1]), float(row[2])

    def save_model_state(
        self,
        key: Union[Tuple[str, str, str], str],
        n: int,
        mean: float,
        var: float,
        file_class: Optional[str] = None,
        tool_profile: Optional[str] = None,
    ) -> None:
        """Persist or update EWMA model state (n, mean, var)."""
        if isinstance(key, tuple):
            proj_id, fc, tp = key
        else:
            proj_id, fc, tp = key, file_class, tool_profile

        now = time.time()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO model_state (project_id, file_class, tool_profile, n, mean, var, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, file_class, tool_profile) DO UPDATE SET
                        n = excluded.n,
                        mean = excluded.mean,
                        var = excluded.var,
                        updated_at = excluded.updated_at
                    """,
                    (proj_id, fc, tp, int(n), float(mean), float(var), now),
                )

    # --- Predictions & Outcomes ---

    def record_prediction(
        self,
        project_id: str,
        file_class: str,
        tool_profile: str,
        t_save_wall: float,
        window_lo: float,
        window_hi: float,
        armed: bool = False,
        holdout: bool = False,
    ) -> int:
        """Record a prediction window (shadow or armed). Outcome is initially NULL."""
        now = time.time()
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO predictions
                        (project_id, file_class, tool_profile, t_save_wall, window_lo, window_hi,
                         armed, holdout, outcome, observed_delay_s, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                    """,
                    (
                        project_id,
                        file_class,
                        tool_profile,
                        float(t_save_wall),
                        float(window_lo),
                        float(window_hi),
                        1 if armed else 0,
                        1 if holdout else 0,
                        now,
                    ),
                )
                pred_id = cur.lastrowid
                self._write_count += 1
                if self._write_count % self._prune_interval == 0:
                    self.prune()
                return pred_id

    def score_prediction(
        self,
        prediction_id: int,
        outcome: str,
        observed_delay_s: Optional[float] = None,
    ) -> bool:
        """Score an existing prediction with outcome ('hit', 'miss', or 'no_build').

        A 'no_build' outcome counts as a miss when calculating hit rates.
        """
        valid_outcomes = {"hit", "miss", "no_build"}
        if outcome not in valid_outcomes:
            raise ValueError(f"Invalid prediction outcome '{outcome}'. Expected one of {valid_outcomes}")

        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    UPDATE predictions
                    SET outcome = ?, observed_delay_s = ?
                    WHERE id = ?
                    """,
                    (
                        outcome,
                        float(observed_delay_s) if observed_delay_s is not None else None,
                        prediction_id,
                    ),
                )
                return cur.rowcount > 0

    def record_prediction_outcome(self, outcome: Any) -> None:
        """Backward-compatibility helper for recording and scoring in one call."""
        if hasattr(outcome, "prediction_id") and outcome.prediction_id is not None:
            self.score_prediction(outcome.prediction_id, outcome.outcome, outcome.observed_delay)
        else:
            p_id = self.record_prediction(
                project_id=outcome.project_id,
                file_class=getattr(outcome, "file_class", "source"),
                tool_profile=getattr(outcome, "tool_profile", "default"),
                t_save_wall=getattr(outcome, "window_start", time.time()),
                window_lo=getattr(outcome, "window_start", 0.0),
                window_hi=getattr(outcome, "window_end", 0.0),
            )
            self.score_prediction(p_id, outcome.outcome, getattr(outcome, "observed_delay", None))

    def set_prediction_decision(
        self,
        prediction_id: int,
        decision: str,
        contention_level: float,
        benefit_s: float,
        cost_s: float,
    ) -> bool:
        """Store the arming gate decision on a prediction row.

        Valid decision values:
            'would_arm', 'holdout', 'abstain_confidence',
            'abstain_no_contention', 'abstain_cost', 'abstain_no_build_estimate'

        The 'armed' flag is NOT changed here; it stays False in observation-only mode.

        Args:
            prediction_id: Row ID of the prediction to update.
            decision: Arming decision string.
            contention_level: PSI contention level at decision time (percent).
            benefit_s: Estimated benefit in seconds.
            cost_s: Estimated cost in seconds.

        Returns:
            True if a row was updated.
        """
        valid_decisions = {
            "would_arm",
            "armed",
            "holdout",
            "abstain_confidence",
            "abstain_no_contention",
            "abstain_cost",
            "abstain_no_build_estimate",
        }
        if decision not in valid_decisions:
            raise ValueError(
                f"Invalid decision '{decision}'. Expected one of {valid_decisions}"
            )
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    UPDATE predictions
                    SET decision = ?, contention_level = ?, benefit_s = ?, cost_s = ?
                    WHERE id = ?
                    """,
                    (decision, float(contention_level), float(benefit_s), float(cost_s), prediction_id),
                )
                return cur.rowcount > 0

    def set_prediction_armed(self, prediction_id: int, armed: bool) -> bool:
        """Update the armed flag on an existing prediction row.

        Args:
            prediction_id: Row ID of the prediction to update.
            armed: New armed value.

        Returns:
            True if a row was updated.
        """
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE predictions SET armed = ? WHERE id = ?",
                    (1 if armed else 0, prediction_id),
                )
                return cur.rowcount > 0

    def get_prediction(self, prediction_id: int) -> Optional[Dict[str, Any]]:
        """Fetch prediction row by ID."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM predictions WHERE id = ?",
                (prediction_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    def set_prediction_holdout(self, prediction_id: int, holdout: bool) -> bool:
        """Update the holdout flag on an existing prediction row.

        Args:
            prediction_id: Row ID of the prediction to update.
            holdout: New holdout value.

        Returns:
            True if a row was updated.
        """
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE predictions SET holdout = ? WHERE id = ?",
                    (1 if holdout else 0, prediction_id),
                )
                return cur.rowcount > 0

    def decision_counts(self, project_id: str) -> dict:
        """Return counts of each arming decision for a project.

        Args:
            project_id: Project identifier.

        Returns:
            Dict mapping decision string to count, e.g.
            {'would_arm': 5, 'abstain_no_contention': 10, ...}.
            Always contains all 6 valid decision keys (with 0 for missing).
        """
        valid_decisions = [
            "would_arm",
            "holdout",
            "abstain_confidence",
            "abstain_no_contention",
            "abstain_cost",
            "abstain_no_build_estimate",
        ]
        result = {d: 0 for d in valid_decisions}
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT decision, COUNT(*) FROM predictions
                WHERE project_id = ? AND decision IS NOT NULL
                GROUP BY decision
                """,
                (project_id,),
            )
            for row in cur.fetchall():
                decision, count = row
                result[decision] = count
        return result


    def recent_hit_rate(
        self,
        project_id: str,
        file_class: str,
        tool_profile: str,
        last: int = 10,
    ) -> Tuple[int, int, float]:
        """Compute rolling hit rate over the most recent scored predictions.

        Returns:
            Tuple of (hits, scored_total, rate).
            Only scored predictions count (outcome IS NOT NULL).
            A 'no_build' outcome counts as a miss.
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT outcome FROM predictions
                WHERE project_id = ? AND file_class = ? AND tool_profile = ? AND outcome IS NOT NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (project_id, file_class, tool_profile, last),
            )
            rows = cur.fetchall()

        scored_total = len(rows)
        if scored_total == 0:
            return 0, 0, 0.0

        hits = sum(1 for (out,) in rows if out == "hit")
        rate = hits / float(scored_total)
        return hits, scored_total, rate

    def get_recent_outcomes(
        self,
        project_id: str,
        limit: int = 10,
    ) -> List[PredictionRecord]:
        """Backward-compatibility method to retrieve recent scored predictions."""
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT id, project_id, file_class, tool_profile, t_save_wall, window_lo, window_hi,
                       armed, holdout, outcome, observed_delay_s, created_at
                FROM predictions
                WHERE project_id = ? AND outcome IS NOT NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (project_id, limit),
            )
            rows = cur.fetchall()
            return [
                PredictionRecord(
                    id=r[0],
                    project_id=r[1],
                    file_class=r[2],
                    tool_profile=r[3],
                    t_save_wall=r[4],
                    window_lo=r[5],
                    window_hi=r[6],
                    armed=bool(r[7]),
                    holdout=bool(r[8]),
                    outcome=r[9],
                    observed_delay_s=r[10],
                    created_at=r[11],
                )
                for r in rows
            ]

    def prediction_count(
        self,
        project_id: str,
        file_class: str,
        tool_profile: str,
        scored_only: bool = True,
    ) -> int:
        """Return total count of predictions (for the 'once 5 predictions exist' rule)."""
        with self._lock:
            if scored_only:
                cur = self._conn.execute(
                    """
                    SELECT COUNT(*) FROM predictions
                    WHERE project_id = ? AND file_class = ? AND tool_profile = ? AND outcome IS NOT NULL
                    """,
                    (project_id, file_class, tool_profile),
                )
            else:
                cur = self._conn.execute(
                    """
                    SELECT COUNT(*) FROM predictions
                    WHERE project_id = ? AND file_class = ? AND tool_profile = ?
                    """,
                    (project_id, file_class, tool_profile),
                )
            return cur.fetchone()[0]

    # --- Builds ---

    def record_build(
        self,
        project_id: str,
        condition: str,
        contention_level: float,
        duration_s: float,
        t_start_wall: float,
        t_end_wall: float,
        prediction_id: Optional[int] = None,
    ) -> int:
        """Persist a build run to feed the Stage 9 benefit estimator."""
        valid_conditions = {"reserved", "holdout", "observed"}
        if condition not in valid_conditions:
            raise ValueError(f"Invalid condition '{condition}'. Expected one of {valid_conditions}")

        now = time.time()
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO builds
                        (project_id, condition, contention_level, duration_s,
                         t_start_wall, t_end_wall, prediction_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        condition,
                        float(contention_level),
                        float(duration_s),
                        float(t_start_wall),
                        float(t_end_wall),
                        prediction_id,
                        now,
                    ),
                )
                return cur.lastrowid

    def builds_by_condition(self, project_id: str, condition: str) -> List[BuildRecord]:
        """Fetch all build records for a given project and condition."""
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT id, project_id, condition, contention_level, duration_s,
                       t_start_wall, t_end_wall, prediction_id, created_at
                FROM builds
                WHERE project_id = ? AND condition = ?
                ORDER BY id ASC
                """,
                (project_id, condition),
            )
            rows = cur.fetchall()
            return [
                BuildRecord(
                    id=r[0],
                    project_id=r[1],
                    condition=r[2],
                    contention_level=r[3],
                    duration_s=r[4],
                    t_start_wall=r[5],
                    t_end_wall=r[6],
                    prediction_id=r[7],
                    created_at=r[8],
                )
                for r in rows
            ]

    def recent_build_durations(
        self,
        project_id: str,
        condition: str = "observed",
        n: int = 10,
    ) -> List[float]:
        """Return up to n most recent build durations for a project and condition.

        Used by ArmingGate to compute median build duration for cost/benefit model.

        Args:
            project_id: Project identifier.
            condition: Build condition to filter by (default 'observed').
            n: Maximum number of durations to return.

        Returns:
            List of duration_s floats, most recent first.
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT duration_s FROM builds
                WHERE project_id = ? AND condition = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (project_id, condition, n),
            )
            return [row[0] for row in cur.fetchall()]

    # --- Retention & Pruning ---


    def prune(
        self,
        retention_samples: Optional[int] = None,
        retention_predictions: Optional[int] = None,
    ) -> Dict[str, int]:
        """Prune delay_samples and predictions to retain only the most recent N rows per key."""
        limit_samples = (
            retention_samples
            if retention_samples is not None
            else self.config.retention_samples_per_key
        )
        limit_preds = (
            retention_predictions
            if retention_predictions is not None
            else self.config.retention_predictions_per_key
        )

        with self._lock:
            with self._conn:
                cur_s = self._conn.execute(
                    """
                    DELETE FROM delay_samples
                    WHERE id IN (
                        SELECT id FROM (
                            SELECT id,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY project_id, file_class, tool_profile
                                       ORDER BY id DESC
                                   ) as rn
                            FROM delay_samples
                        ) WHERE rn > ?
                    )
                    """,
                    (limit_samples,),
                )
                pruned_s = cur_s.rowcount

                cur_p = self._conn.execute(
                    """
                    DELETE FROM predictions
                    WHERE id IN (
                        SELECT id FROM (
                            SELECT id,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY project_id, file_class, tool_profile
                                       ORDER BY id DESC
                                   ) as rn
                            FROM predictions
                        ) WHERE rn > ?
                    )
                    """,
                    (limit_preds,),
                )
                pruned_p = cur_p.rowcount

        return {"pruned_samples": pruned_s, "pruned_predictions": pruned_p}

    # --- Reservations Management (Stage 8) ---

    def record_reservation(self, record: Any) -> None:
        """Record or update a reservation entry in the database."""
        if hasattr(record, "record"):
            record = record.record

        res_id = getattr(record, "id", None) or (record.get("id") if isinstance(record, dict) else None)
        project_id = getattr(record, "project_id", None) or (record.get("project_id") if isinstance(record, dict) else None)
        prediction_id = getattr(record, "prediction_id", None) if hasattr(record, "prediction_id") else (record.get("prediction_id") if isinstance(record, dict) else None)
        final_state = getattr(record, "final_state", None) or (record.get("final_state") if isinstance(record, dict) else None)
        if final_state is None:
            state = getattr(record, "state", None) or (record.get("state") if isinstance(record, dict) else None)
            final_state = state.name if hasattr(state, "name") else str(state)
        weight = getattr(record, "weight", 500) if hasattr(record, "weight") else (record.get("weight", 500) if isinstance(record, dict) else 500)
        armed_at = getattr(record, "created_at", None) or (record.get("armed_at", time.time()) if isinstance(record, dict) else time.time())
        window_lo_abs = getattr(record, "window_lo_abs", 0.0) if hasattr(record, "window_lo_abs") else (record.get("window_lo_abs_s", 0.0) if isinstance(record, dict) else 0.0)
        window_hi_abs = getattr(record, "window_hi_abs", 0.0) if hasattr(record, "window_hi_abs") else (record.get("window_hi_abs_s", 0.0) if isinstance(record, dict) else 0.0)
        claimed_at = getattr(record, "claimed_at", None) if hasattr(record, "claimed_at") else (record.get("claimed_at") if isinstance(record, dict) else None)
        claim_latency_s = getattr(record, "claim_latency_s", None) if hasattr(record, "claim_latency_s") else (record.get("claim_latency_s") if isinstance(record, dict) else None)
        claimed_pid = getattr(record, "claimed_pid", None) if hasattr(record, "claimed_pid") else (record.get("claimed_pid") if isinstance(record, dict) else None)
        closed_at = getattr(record, "closed_at", None) if hasattr(record, "closed_at") else (record.get("closed_at") if isinstance(record, dict) else None)
        group_name = getattr(record, "group_name", "") if hasattr(record, "group_name") else (record.get("group_name", "") if isinstance(record, dict) else "")
        original_cgroup = getattr(record, "original_cgroup", None) if hasattr(record, "original_cgroup") else (record.get("original_cgroup") if isinstance(record, dict) else None)
        reason = getattr(record, "reason", "") if hasattr(record, "reason") else (record.get("reason", "") if isinstance(record, dict) else "")

        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO reservations (
                        id, project_id, prediction_id, final_state, weight,
                        armed_at, window_lo_abs_s, window_hi_abs_s, claimed_at,
                        claim_latency_s, claimed_pid, closed_at, group_name,
                        original_cgroup, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        final_state=excluded.final_state,
                        claimed_at=excluded.claimed_at,
                        claim_latency_s=excluded.claim_latency_s,
                        claimed_pid=excluded.claimed_pid,
                        closed_at=excluded.closed_at,
                        reason=excluded.reason
                    """,
                    (
                        res_id, project_id, prediction_id, final_state, weight,
                        armed_at, window_lo_abs, window_hi_abs, claimed_at,
                        claim_latency_s, claimed_pid, closed_at, group_name,
                        original_cgroup, reason
                    ),
                )

    def get_reservation(self, reservation_id: str) -> Optional[Dict[str, Any]]:
        """Fetch reservation by ID."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM reservations WHERE id = ?",
                (reservation_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    def list_reservations(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all recorded reservations, optionally filtered by project_id."""
        with self._lock:
            if project_id:
                cur = self._conn.execute(
                    "SELECT * FROM reservations WHERE project_id = ? ORDER BY armed_at DESC",
                    (project_id,),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM reservations ORDER BY armed_at DESC"
                )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def reservation_summary(self, project_id: Optional[str] = None) -> Dict[str, Any]:
        """Summary of reservations: counts by final_state and mean claim latency."""
        with self._lock:
            if project_id:
                cur = self._conn.execute(
                    "SELECT final_state, COUNT(*), AVG(claim_latency_s) FROM reservations WHERE project_id = ? GROUP BY final_state",
                    (project_id,),
                )
                cur_lat = self._conn.execute(
                    "SELECT AVG(claim_latency_s) FROM reservations WHERE project_id = ? AND claim_latency_s IS NOT NULL",
                    (project_id,),
                )
            else:
                cur = self._conn.execute(
                    "SELECT final_state, COUNT(*), AVG(claim_latency_s) FROM reservations GROUP BY final_state"
                )
                cur_lat = self._conn.execute(
                    "SELECT AVG(claim_latency_s) FROM reservations WHERE claim_latency_s IS NOT NULL"
                )

            counts: Dict[str, int] = {}
            for row in cur.fetchall():
                counts[row[0]] = row[1]

            lat_row = cur_lat.fetchone()
            mean_latency = lat_row[0] if (lat_row and lat_row[0] is not None) else None

            return {
                "counts": counts,
                "mean_claim_latency_s": mean_latency,
                "total": sum(counts.values()),
            }

    # --- Benefit State & Evaluation (Stage 9) ---

    def get_benefit_state(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Fetch current benefit state for a project."""
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT project_id, weight, arming_enabled, status, last_effect,
                       last_p_value, n_reserved, n_holdout, last_eval_at, updated_at
                FROM benefit_state WHERE project_id = ?
                """,
                (project_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "project_id": row[0],
                "weight": row[1],
                "arming_enabled": bool(row[2]),
                "status": row[3],
                "last_effect": row[4],
                "last_p_value": row[5],
                "n_reserved": row[6],
                "n_holdout": row[7],
                "last_eval_at": row[8],
                "updated_at": row[9],
            }

    def set_benefit_state(
        self,
        project_id: str,
        weight: int,
        arming_enabled: bool,
        status: str,
        last_effect: Optional[float] = None,
        last_p_value: Optional[float] = None,
        n_reserved: int = 0,
        n_holdout: int = 0,
        last_eval_at: Optional[float] = None,
    ) -> None:
        """Insert or replace benefit state for a project."""
        now = time.time()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO benefit_state (
                        project_id, weight, arming_enabled, status, last_effect,
                        last_p_value, n_reserved, n_holdout, last_eval_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        weight = excluded.weight,
                        arming_enabled = excluded.arming_enabled,
                        status = excluded.status,
                        last_effect = excluded.last_effect,
                        last_p_value = excluded.last_p_value,
                        n_reserved = excluded.n_reserved,
                        n_holdout = excluded.n_holdout,
                        last_eval_at = excluded.last_eval_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        project_id,
                        weight,
                        1 if arming_enabled else 0,
                        status,
                        last_effect,
                        last_p_value,
                        n_reserved,
                        n_holdout,
                        last_eval_at,
                        now,
                    ),
                )

    def record_benefit_eval(
        self,
        project_id: str,
        evaluated_at: float,
        n_reserved: int,
        n_holdout: int,
        per_bin_json: str,
        overall_effect: float,
        ci_low: float,
        ci_high: float,
        p_value: float,
        action: str,
        weight_after: int,
        arming_after: bool,
    ) -> int:
        """Record a benefit evaluation run."""
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO benefit_evals (
                        project_id, evaluated_at, n_reserved, n_holdout,
                        per_bin_json, overall_effect, ci_low, ci_high,
                        p_value, action, weight_after, arming_after
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        evaluated_at,
                        n_reserved,
                        n_holdout,
                        per_bin_json,
                        overall_effect,
                        ci_low,
                        ci_high,
                        p_value,
                        action,
                        weight_after,
                        1 if arming_after else 0,
                    ),
                )
                return cur.lastrowid

    def list_benefit_evals(self, project_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """List historical benefit evaluations for a project."""
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT id, project_id, evaluated_at, n_reserved, n_holdout,
                       per_bin_json, overall_effect, ci_low, ci_high,
                       p_value, action, weight_after, arming_after
                FROM benefit_evals WHERE project_id = ?
                ORDER BY evaluated_at DESC LIMIT ?
                """,
                (project_id, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": r[0],
                    "project_id": r[1],
                    "evaluated_at": r[2],
                    "n_reserved": r[3],
                    "n_holdout": r[4],
                    "per_bin_json": r[5],
                    "overall_effect": r[6],
                    "ci_low": r[7],
                    "ci_high": r[8],
                    "p_value": r[9],
                    "action": r[10],
                    "weight_after": r[11],
                    "arming_after": bool(r[12]),
                }
                for r in rows
            ]

    # --- CSV Export ---

    def export_csv(self, table: str, path: Union[str, Path]) -> int:
        """Export table contents to CSV with header row. Returns rows exported."""
        table_name = table.strip().lower()
        allowed = {
            "projects",
            "delay_samples",
            "model_state",
            "predictions",
            "builds",
            "reservations",
            "benefit_state",
            "benefit_evals",
            "daemon_runs",
        }
        if table_name not in allowed:
            raise ValueError(f"Table '{table_name}' is not allowed for export. Allowed: {allowed}")

        target_path = Path(path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            cur = self._conn.execute(f"SELECT * FROM {table_name}")
            col_names = [desc[0] for desc in cur.description]
            rows = cur.fetchall()

        with open(target_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(col_names)
            writer.writerows(rows)

        return len(rows)

    # --- Rolling Budget & Daemon Runs (Stage 10) ---

    def get_boost_duration_in_window(
        self, window_seconds: float = 3600.0, now: Optional[float] = None
    ) -> float:
        """Calculate total boosted time (in seconds) consumed across reservations within the rolling window."""
        t_now = time.time() if now is None else float(now)
        cutoff = t_now - float(window_seconds)
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT claimed_at, closed_at FROM reservations
                WHERE claimed_at IS NOT NULL AND (closed_at >= ? OR closed_at IS NULL)
                """,
                (cutoff,),
            )
            rows = cur.fetchall()

        total = 0.0
        for claimed_at, closed_at in rows:
            t_claim = float(claimed_at)
            t_close = float(closed_at) if closed_at is not None else t_now
            start = max(t_claim, cutoff)
            end = min(t_close, t_now)
            if end > start:
                total += (end - start)
        return total

    def record_daemon_run(
        self,
        condition: str,
        flags_json: str,
        code_version: str,
        started_at: Optional[float] = None,
    ) -> int:
        """Record a daemon execution session in daemon_runs table."""
        t_start = time.time() if started_at is None else float(started_at)
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO daemon_runs (started_at, condition, flags_json, code_version)
                    VALUES (?, ?, ?, ?)
                    """,
                    (t_start, condition, flags_json, code_version),
                )
                return int(cur.lastrowid)

    def list_daemon_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List recent daemon runs."""
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT id, started_at, condition, flags_json, code_version
                FROM daemon_runs ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": r[0],
                    "started_at": r[1],
                    "condition": r[2],
                    "flags_json": r[3],
                    "code_version": r[4],
                }
                for r in rows
            ]


Store = DataStore
