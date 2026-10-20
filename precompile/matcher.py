"""Identity matching engine for processes and project tool profiles.

Stage Mapping: Stage 2 (Process-Creation Observation) & Stage 7 (Identity-Bound Claiming).
Validates process executables against tool profiles, verifies canonical working directories,
and resolves topmost ancestor processes within the project.

Design Note:
    All core matching and resolution functions in this module are pure functions taking
    data in and returning results. The only filesystem/kernel read is the /proc/<pid>/cgroup
    membership check, which is abstracted behind a small pluggable reader function
    (`get_proc_cgroup`) that tests can replace without touching the host filesystem.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

from precompile.watcher import ProcessInfo, ProcessSpawnEvent

DEFAULT_TOOL_BINARIES: Set[str] = {
    "make",
    "gmake",
    "cmake",
    "ninja",
    "gcc",
    "g++",
    "cc",
    "c++",
    "clang",
    "clang++",
}

# Regex pattern for cross-compilation target prefixes (e.g. x86_64-linux-gnu-, arm-none-eabi-)
# Matches standard triplets/quadruplets ending in a hyphen
TARGET_PREFIX_PATTERN = r"(?:[a-zA-Z0-9_]+-(?:[a-zA-Z0-9_]+-)*(?:linux|gnu|elf|eabi|musl|uclibc|none|w64|mingw32)-)?"

# Regex pattern for version suffixes (e.g. -13, -18.1)
VERSION_SUFFIX_PATTERN = r"(?:-[0-9]+(?:\.[0-9]+)*)?"


def is_tool_match(candidate_name: str, allowed_binaries: Set[str]) -> bool:
    """Check if candidate executable basename matches any allowed tool.

    Supports exact matches, versioned names (gcc-13, clang-18), and target-prefixed
    cross-compilers (x86_64-linux-gnu-gcc-13), while rejecting non-conforming names (e.g. gccx).
    """
    if candidate_name in allowed_binaries:
        return True

    for tool in allowed_binaries:
        # Exact match or tool with version/target prefix
        # We escape the tool base name to safely handle characters like '++' in g++ and c++
        escaped_tool = re.escape(tool)
        pattern = rf"^{TARGET_PREFIX_PATTERN}{escaped_tool}{VERSION_SUFFIX_PATTERN}$"
        if re.match(pattern, candidate_name):
            return True

    return False


@dataclass
class ToolProfile:
    """Defines recognized build tools and compiler binaries for a project."""
    name: str = "default"
    binaries: Set[str] = field(default_factory=lambda: set(DEFAULT_TOOL_BINARIES))

    def matches_binary(self, binary_name: str) -> bool:
        """Check if binary_name matches any tool defined in this profile."""
        return is_tool_match(binary_name, self.binaries)


DEFAULT_TOOL_PROFILE = ToolProfile()


def resolve_tool_profile(name: Optional[str]) -> ToolProfile:
    """Resolve a tool profile name string into a ToolProfile instance.

    Supports:
        - "default" / None / "": default tool profile (gcc, clang, make, ninja, etc.)
        - "custom:gcc,make,ninja": custom profile with explicitly specified tool binaries
        - "gcc,make": comma-separated list converted to custom profile
    """
    if not name or name == "default":
        return DEFAULT_TOOL_PROFILE
    if name.startswith("custom:"):
        raw_tools = name[len("custom:"):].strip()
        binaries = {t.strip() for t in raw_tools.split(",") if t.strip()}
        return ToolProfile(name=name, binaries=binaries)
    if "," in name:
        binaries = {t.strip() for t in name.split(",") if t.strip()}
        return ToolProfile(name=f"custom:{','.join(sorted(binaries))}", binaries=binaries)
    return ToolProfile(name=name, binaries={name.strip()})


def get_proc_cgroup(pid: int) -> str:
    """Read /proc/<pid>/cgroup content. Replaceable for testing."""
    try:
        with open(f"/proc/{pid}/cgroup", "r", encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return ""


def is_in_precompile_cgroup(
    pid: int,
    cgroup_prefix: str = "precompile",
    cgroup_reader: Optional[Callable[[int], str]] = None,
) -> bool:
    """Check if process is already running inside a PreCompile-managed cgroup.

    Parses /proc/<pid>/cgroup looking for cgroup path segments matching cgroup_prefix.
    """
    reader = cgroup_reader if cgroup_reader is not None else get_proc_cgroup
    content = reader(pid)
    if not content:
        return False

    for line in content.splitlines():
        parts = line.strip().split(":", 2)
        if len(parts) == 3:
            cg_path = parts[2]
            segments = [seg for seg in cg_path.split("/") if seg]
            for seg in segments:
                if seg == cgroup_prefix or seg.startswith(f"{cgroup_prefix}_") or seg.startswith(f"{cgroup_prefix}-"):
                    return True
    return False


def is_path_inside(path: Union[str, Path], root: Union[str, Path]) -> bool:
    """Check if path is strictly inside root or equal to root after resolving symlinks.

    Avoids the string-prefix trap (e.g. distinguishing /proj from /proj-other).
    """
    resolved_path = Path(os.path.realpath(path))
    resolved_root = Path(os.path.realpath(root))
    try:
        resolved_path.relative_to(resolved_root)
        return True
    except ValueError:
        return False


def match(
    process: Union[ProcessInfo, ProcessSpawnEvent],
    project_root: Union[str, Path],
    tool_profile: Optional[ToolProfile] = None,
    cgroup_reader: Optional[Callable[[int], str]] = None,
    cgroup_prefix: str = "precompile",
) -> bool:
    """Determine if a process matches the tool profile and working directory of a project.

    Returns True only if ALL of the following hold:
    1. Executable basename is in the tool profile (including regex version/prefix matches).
    2. Working directory (cwd), resolved with symlinks (os.path.realpath), is inside project_root.
    3. The process is not already inside a PreCompile cgroup.

    Args:
        process: ProcessInfo or ProcessSpawnEvent to test.
        project_root: Root directory of the target project.
        tool_profile: ToolProfile to validate against (defaults to DEFAULT_TOOL_PROFILE).
        cgroup_reader: Optional override callable to read /proc/<pid>/cgroup.
        cgroup_prefix: Cgroup path prefix indicating PreCompile management (default: 'precompile').

    Returns:
        True if all conditions match; False otherwise.
    """
    profile = tool_profile if tool_profile is not None else DEFAULT_TOOL_PROFILE

    # 1. Executable basename match
    exe_name = Path(process.exe).name
    if not profile.matches_binary(exe_name):
        return False

    # 2. Canonical working directory containment check
    if not is_path_inside(process.cwd, project_root):
        return False

    # 3. Process is not already inside a PreCompile cgroup
    if is_in_precompile_cgroup(process.pid, cgroup_prefix=cgroup_prefix, cgroup_reader=cgroup_reader):
        return False

    return True


def _get_ppid_from_table(
    pid: int,
    process_table: Union[Dict[int, Any], Dict[int, int]],
) -> Optional[int]:
    """Extract PPID from a process table (mapping pid -> ProcessInfo or pid -> ppid)."""
    entry = process_table.get(pid)
    if entry is None:
        return None
    if isinstance(entry, int):
        return entry
    return getattr(entry, "ppid", None)


def resolve_topmost_ancestor(
    candidates: List[Union[ProcessInfo, ProcessSpawnEvent]],
    process_table: Union[Dict[int, Any], Dict[int, int]],
) -> Optional[Union[ProcessInfo, ProcessSpawnEvent]]:
    """Resolve the topmost ancestor process among simultaneous candidates.

    Returns the candidate that has no ancestor among the candidate set.
    If multiple roots remain (e.g. two independent tool invocations),
    returns the one with the earliest start time (t_start_monotonic).

    Args:
        candidates: List of matching candidate processes.
        process_table: Mapping of PID to ProcessInfo or PID to parent PID (PPID).

    Returns:
        The single topmost candidate ProcessInfo, or None if candidates list is empty.
    """
    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0]

    candidate_pids = {c.pid for c in candidates}

    def has_candidate_ancestor(pid: int) -> bool:
        curr = pid
        visited = {curr}
        while True:
            ppid = _get_ppid_from_table(curr, process_table)
            if ppid is None or ppid <= 0 or ppid in visited:
                break
            if ppid in candidate_pids:
                return True
            visited.add(ppid)
            curr = ppid
        return False

    roots = [c for c in candidates if not has_candidate_ancestor(c.pid)]

    pool = roots if roots else candidates

    def get_start_time(proc: Union[ProcessInfo, ProcessSpawnEvent]) -> float:
        return getattr(proc, "t_start_monotonic", getattr(proc, "timestamp", 0.0))

    return min(pool, key=get_start_time)


def get_descendants(
    pid: int,
    process_table: Union[Dict[int, Any], Dict[int, int]],
) -> Set[int]:
    """Retrieve all existing descendant PIDs of a given PID from the process table / parent map.

    Excludes siblings and ancestors, returning only direct and indirect children.

    Args:
        pid: Root process PID.
        process_table: Mapping of PID to ProcessInfo or PID to parent PID.

    Returns:
        Set of descendant PIDs.
    """
    children: Dict[int, List[int]] = {}
    for p, entry in process_table.items():
        ppid = entry if isinstance(entry, int) else getattr(entry, "ppid", None)
        if ppid is not None:
            children.setdefault(ppid, []).append(p)

    descendants: Set[int] = set()
    queue = [pid]
    while queue:
        curr = queue.pop(0)
        for child in children.get(curr, []):
            if child not in descendants:
                descendants.add(child)
                queue.append(child)

    return descendants


class IdentityMatcher:
    """Matches spawned processes against registered project profiles and directory roots."""

    def __init__(
        self,
        cgroup_reader: Optional[Callable[[int], str]] = None,
        cgroup_prefix: str = "precompile",
    ) -> None:
        """Initialize the IdentityMatcher."""
        self._projects: Dict[str, Tuple[Path, List[ToolProfile]]] = {}
        self._cgroup_reader = cgroup_reader
        self._cgroup_prefix = cgroup_prefix

    def register_project_target(
        self,
        project_id: str,
        root_path: Path,
        tool_profiles: List[ToolProfile],
    ) -> None:
        """Register a project target with its root directory and valid tool profiles.

        Args:
            project_id: Unique identifier for the project.
            root_path: Filesystem path to the project root.
            tool_profiles: List of recognized tool profiles.
        """
        self._projects[project_id] = (Path(root_path), list(tool_profiles))

    def unregister_project_target(self, project_id: str) -> None:
        """Remove a project registration."""
        self._projects.pop(project_id, None)

    def matches_project(
        self,
        event: Union[ProcessInfo, ProcessSpawnEvent],
        project_id: str,
    ) -> bool:
        """Determine if a process matches the tool profile and working directory of a project."""
        if project_id not in self._projects:
            return False

        root_path, profiles = self._projects[project_id]
        if not profiles:
            profiles = [DEFAULT_TOOL_PROFILE]

        return any(
            match(
                process=event,
                project_root=root_path,
                tool_profile=prof,
                cgroup_reader=self._cgroup_reader,
                cgroup_prefix=self._cgroup_prefix,
            )
            for prof in profiles
        )

    def resolve_topmost_ancestor(
        self,
        candidates: List[Union[ProcessInfo, ProcessSpawnEvent]],
        process_tree: Union[Dict[int, Any], Dict[int, int]],
    ) -> Optional[Union[ProcessInfo, ProcessSpawnEvent]]:
        """Resolve the topmost ancestor among multiple matching concurrent processes."""
        return resolve_topmost_ancestor(candidates, process_tree)

    def get_descendants(
        self,
        pid: int,
        process_tree: Union[Dict[int, Any], Dict[int, int]],
    ) -> Set[int]:
        """Retrieve all descendant PIDs of a given parent PID."""
        return get_descendants(pid, process_tree)
