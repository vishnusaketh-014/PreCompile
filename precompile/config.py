"""Configuration management for PreCompile.

Stage Mapping: Stage 10 (Safety, Bounded Harm & Fallback) & Global Settings.
Handles loading, validation, and defaults from config.yaml.
"""

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("precompile.config")

DEFAULT_IGNORED_DIRECTORIES: Set[str] = {
    ".git",
    ".hg",
    ".svn",
    "build",
    "target",
    "dist",
    "out",
    "node_modules",
    "__pycache__",
    ".cache",
}

DEFAULT_IGNORED_FILE_PATTERNS: Set[str] = {
    "*.swp",
    "*.swx",
    "*~",
    ".#*",
    "#*#",
    "*.tmp",
    "*.o",
    "*.d",
    "*.a",
    "*.so",
    "*.pyc",
}

SOURCE_EXTENSIONS: Set[str] = {".c", ".cc", ".cpp", ".cxx"}
HEADER_EXTENSIONS: Set[str] = {".h", ".hh", ".hpp", ".hxx"}


def is_ignored_directory(name_or_path: Union[str, Path], ignored_dirs: Optional[Set[str]] = None) -> bool:
    """Check if a directory name or any path component matches ignored directory rules."""
    dirs = ignored_dirs if ignored_dirs is not None else DEFAULT_IGNORED_DIRECTORIES
    if isinstance(name_or_path, str):
        parts = Path(name_or_path).parts
    else:
        parts = name_or_path.parts
    return any(p in dirs for p in parts)


def is_ignored_file(name_or_path: Union[str, Path], ignored_patterns: Optional[Set[str]] = None) -> bool:
    """Check if a file matches ignored file patterns or is a numeric vim probe file."""
    filename = Path(name_or_path).name
    # Numeric vim probe files (e.g. 4913, 4914)
    if filename.isdigit():
        return True
    patterns = ignored_patterns if ignored_patterns is not None else DEFAULT_IGNORED_FILE_PATTERNS
    return any(fnmatch.fnmatch(filename, pat) for pat in patterns)


def classify_file(path: Union[str, Path]) -> str:
    """Classify file as 'source', 'header', or 'other' based on file extension."""
    suffix = Path(path).suffix.lower()
    if suffix in SOURCE_EXTENSIONS:
        return "source"
    elif suffix in HEADER_EXTENSIONS:
        return "header"
    return "other"


@dataclass
class MonitorConfig:
    """Filesystem event monitor configuration (Stage 1)."""
    debounce_seconds: float = 0.15
    ignored_directories: Set[str] = field(default_factory=lambda: set(DEFAULT_IGNORED_DIRECTORIES))
    ignored_file_patterns: Set[str] = field(default_factory=lambda: set(DEFAULT_IGNORED_FILE_PATTERNS))


@dataclass
class PredictorConfig:
    """Predictor and learning hyperparameters (Stages 3 & 4)."""
    alpha: float = 0.2
    k: float = 2.0
    margin_m: float = 0.3
    clip_sigma: float = 3.0
    clip_min_samples: int = 5
    clip_sigma_floor_s: float = 0.1
    correlation_window_s: float = 30.0
    min_samples: int = 5
    cv_max: float = 0.5
    sigma_max_small_s: float = 0.25
    hit_rate_min: float = 0.6
    hit_rate_last: int = 10
    min_predictions: int = 5
    strict_min_predictions_to_arm: bool = True
    learned_file_classes: Tuple[str, ...] = ("source", "header")
    negative_delay_tolerance_s: float = 0.02

    def __init__(
        self,
        alpha: float = 0.2,
        k: float = 2.0,
        margin_m: Optional[float] = None,
        clip_sigma: Optional[float] = None,
        clip_min_samples: int = 5,
        clip_sigma_floor_s: float = 0.1,
        correlation_window_s: Optional[float] = None,
        min_samples: int = 5,
        cv_max: Optional[float] = None,
        sigma_max_small_s: Optional[float] = None,
        hit_rate_min: Optional[float] = None,
        hit_rate_last: Optional[int] = None,
        min_predictions: int = 5,
        strict_min_predictions_to_arm: bool = True,
        learned_file_classes: Tuple[str, ...] = ("source", "header"),
        negative_delay_tolerance_s: float = 0.02,
        # Legacy aliases
        m_seconds: Optional[float] = None,
        outlier_sigma_clip: Optional[float] = None,
        correlation_window_seconds: Optional[float] = None,
        max_cv: Optional[float] = None,
        small_mean_sigma_threshold: Optional[float] = None,
        min_hit_rate: Optional[float] = None,
        hit_rate_window: Optional[int] = None,
    ) -> None:
        self.alpha = alpha
        self.k = k
        self.margin_m = margin_m if margin_m is not None else (m_seconds if m_seconds is not None else 0.3)
        self.clip_sigma = clip_sigma if clip_sigma is not None else (outlier_sigma_clip if outlier_sigma_clip is not None else 3.0)
        self.clip_min_samples = clip_min_samples
        self.clip_sigma_floor_s = clip_sigma_floor_s
        self.correlation_window_s = correlation_window_s if correlation_window_s is not None else (correlation_window_seconds if correlation_window_seconds is not None else 30.0)
        self.min_samples = min_samples
        self.cv_max = cv_max if cv_max is not None else (max_cv if max_cv is not None else 0.5)
        self.sigma_max_small_s = sigma_max_small_s if sigma_max_small_s is not None else (small_mean_sigma_threshold if small_mean_sigma_threshold is not None else 0.25)
        self.hit_rate_min = hit_rate_min if hit_rate_min is not None else (min_hit_rate if min_hit_rate is not None else 0.6)
        self.hit_rate_last = hit_rate_last if hit_rate_last is not None else (hit_rate_window if hit_rate_window is not None else 10)
        self.min_predictions = min_predictions
        self.strict_min_predictions_to_arm = strict_min_predictions_to_arm
        self.learned_file_classes = tuple(learned_file_classes)
        self.negative_delay_tolerance_s = negative_delay_tolerance_s

    @property
    def m_seconds(self) -> float:
        return self.margin_m

    @property
    def outlier_sigma_clip(self) -> float:
        return self.clip_sigma

    @property
    def correlation_window_seconds(self) -> float:
        return self.correlation_window_s

    @property
    def max_cv(self) -> float:
        return self.cv_max

    @property
    def small_mean_sigma_threshold(self) -> float:
        return self.sigma_max_small_s

    @property
    def min_hit_rate(self) -> float:
        return self.hit_rate_min

    @property
    def hit_rate_window(self) -> int:
        return self.hit_rate_last


@dataclass
class SafetyConfig:
    """Safety and resource limits (Stage 10)."""
    max_cgroup_weight: int = 1000
    default_cgroup_weight: int = 500
    max_boost_duration_seconds: float = 120.0
    budget_max_elevated_seconds_per_hour: float = 900.0
    budget_interval_s: float = 3600.0
    enable_cgroups: bool = True
    cgroup_root: Path = Path("/sys/fs/cgroup/precompile")

    def __post_init__(self) -> None:
        """Validate and clamp weights according to safety rules."""
        if self.max_cgroup_weight > 1000:
            logger.warning("[safety] Configured max_cgroup_weight %d > 1000; clamping to 1000", self.max_cgroup_weight)
            self.max_cgroup_weight = 1000
        elif self.max_cgroup_weight < 1:
            logger.warning("[safety] Configured max_cgroup_weight %d < 1; clamping to 1", self.max_cgroup_weight)
            self.max_cgroup_weight = 1

        if self.default_cgroup_weight > self.max_cgroup_weight:
            logger.warning(
                "[safety] Configured default_cgroup_weight %d > max_cgroup_weight %d; clamping to %d",
                self.default_cgroup_weight,
                self.max_cgroup_weight,
                self.max_cgroup_weight,
            )
            self.default_cgroup_weight = self.max_cgroup_weight
        elif self.default_cgroup_weight < 1:
            self.default_cgroup_weight = 1


@dataclass
class WatcherConfig:
    """Process watcher configuration (Stage 2)."""
    poll_interval_normal_ms: int = 50
    poll_interval_intensified_ms: int = 10
    cgroup_prefix: str = "precompile"
    grace_period_seconds: float = 5.0
    max_table_size: int = 20000
    sweep_window_s: float = 0.3


def get_default_db_path() -> Path:
    """Return default database path based on user privilege.

    Uses /var/lib/precompile/precompile.db when running as root,
    otherwise ~/.local/share/precompile/precompile.db.
    Never defaults to /mnt/c because SQLite WAL is unreliable on Windows mounts.
    """
    try:
        is_root = (os.geteuid() == 0)
    except AttributeError:
        is_root = False
    if is_root:
        return Path("/var/lib/precompile/precompile.db")
    return Path.home() / ".local" / "share" / "precompile" / "precompile.db"


def validate_store_path(path: Union[str, Path]) -> None:
    """Warn if database path resides on a /mnt/ filesystem mount."""
    path_str = str(Path(path).resolve())
    if path_str.startswith("/mnt/"):
        logger.warning(
            "Configured database path '%s' is on a /mnt/ filesystem mount. "
            "SQLite WAL mode is unreliable on Windows DrvFs mounts; "
            "use native Linux paths (/tmp, /var, ~/.local) instead.",
            path_str,
        )


@dataclass
class StoreConfig:
    """SQLite data store configuration (Stages 3, 4, 8, 9)."""
    db_path: Path = field(default_factory=get_default_db_path)
    retention_samples_per_key: int = 500
    retention_predictions_per_key: int = 200


@dataclass
class AppConfig:
    """Root configuration object loaded from config.yaml."""
    database_path: Path = Path("precompile.db")
    observation_only: bool = True
    actuate: bool = False
    holdout_probability: float = 0.1
    reservation_weight: int = 500
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    watcher: WatcherConfig = field(default_factory=WatcherConfig)
    store: StoreConfig = field(default_factory=StoreConfig)
    predictor: PredictorConfig = field(default_factory=PredictorConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """Load configuration from a YAML file or apply system defaults.

    Args:
        config_path: Path to config.yaml. If None, default search paths are checked.

    Returns:
        Populated AppConfig instance.

    Raises:
        NotImplementedError: Skeleton stage.
    """
    raise NotImplementedError


def save_config(config: AppConfig, target_path: Path) -> None:
    """Save an AppConfig instance to a YAML file.

    Args:
        config: Configuration instance to serialize.
        target_path: Destination path for config.yaml.

    Raises:
        NotImplementedError: Skeleton stage.
    """
    raise NotImplementedError
