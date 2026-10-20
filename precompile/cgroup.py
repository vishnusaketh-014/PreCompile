"""Linux cgroup v2 controller for anticipatory priority reservation.

Stage Mapping: Stage 6 (Anticipatory Reservation) & Stage 7 (Identity-Bound Claiming).

Design Rule:
    cpu.weight in cgroup v2 is strictly hierarchical; a cgroup's weight only competes
    with its direct SIBLINGS under the same parent directory.
    Reservation groups must therefore be placed where they compete directly with ordinary work:
    as direct children of the cgroup v2 root (by default /sys/fs/cgroup), named
    `precompile-res-<id>`. For example, in WSL2 or standard systemd distributions, ordinary
    user sessions run in `/sys/fs/cgroup/init.scope` or `/sys/fs/cgroup/user.slice` with
    cpu.weight=100. A reservation group placed at `/sys/fs/cgroup/precompile-res-<id>` with
    cpu.weight=500 is a direct sibling of `init.scope` under the root, giving it a 5:1 CFS
    CPU time share over ordinary processes on congested cores.

Safety:
    - cpu.weight is clamped to [1, weight_cap] (default cap: 500).
    - Real-time scheduling classes (SCHED_FIFO/SCHED_RR) and nice priorities are NEVER touched.
    - Failures raise CgroupError; operations never crash caller.
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Union

logger = logging.getLogger("precompile.cgroup")


class CgroupError(Exception):
    """Raised when a cgroup filesystem operation fails."""
    pass


class CgroupController:
    """Interfaces with Linux cgroup v2 filesystem to manage reservation groups."""

    def __init__(
        self,
        base_cgroup_path: Union[str, Path] = Path("/sys/fs/cgroup"),
        weight_cap: int = 500,
        default_weight: int = 500,
    ) -> None:
        """Initialize CgroupController.

        Args:
            base_cgroup_path: Parent path for precompile reservations (default /sys/fs/cgroup).
            weight_cap: Maximum allowable cpu.weight value (default 500).
            default_weight: Default cpu.weight applied to newly created groups (default 500).
        """
        self.base_cgroup_path = Path(base_cgroup_path)
        self.weight_cap = max(1, weight_cap)
        self.default_weight = max(1, min(default_weight, self.weight_cap))

    def _clamp_weight(self, weight: Optional[int]) -> int:
        """Clamp weight to [1, weight_cap]."""
        w = weight if weight is not None else self.default_weight
        return max(1, min(int(w), self.weight_cap))

    def ensure_cpu_controller(self) -> None:
        """Ensure the 'cpu' controller is enabled in the parent subtree_control.

        Raises:
            CgroupError: If subtree_control cannot be read or written.
        """
        subtree_file = self.base_cgroup_path / "cgroup.subtree_control"
        if not subtree_file.exists():
            raise CgroupError(f"cgroup.subtree_control not found at {subtree_file}")

        try:
            content = subtree_file.read_text(encoding="utf-8")
            controllers = content.split()
            if "cpu" not in controllers:
                with open(subtree_file, "w", encoding="utf-8") as f:
                    f.write("+cpu\n")
        except OSError as e:
            raise CgroupError(f"Failed to enable cpu controller in {subtree_file}: {e}") from e

    def create_group(self, name: str, weight: Optional[int] = None) -> Path:
        """Create an empty cgroup directory and write its cpu.weight.

        Args:
            name: Group directory name (e.g. 'precompile-res-abc1234').
            weight: Desired cpu.weight (clamped to [1, weight_cap]).

        Returns:
            Path to created cgroup directory.

        Raises:
            CgroupError: If directory creation or weight write fails.
        """
        group_path = self.base_cgroup_path / name
        clamped_w = self._clamp_weight(weight)

        try:
            group_path.mkdir(parents=True, exist_ok=True)
            weight_file = group_path / "cpu.weight"
            weight_file.write_text(f"{clamped_w}\n", encoding="utf-8")
            return group_path
        except OSError as e:
            raise CgroupError(f"Failed to create cgroup {group_path} with weight {clamped_w}: {e}") from e

    def set_weight(self, name: str, weight: int) -> None:
        """Set cpu.weight on an existing cgroup.

        Args:
            name: Group name or relative path.
            weight: New weight value (clamped to [1, weight_cap]).

        Raises:
            CgroupError: If write fails.
        """
        group_path = self.base_cgroup_path / name
        clamped_w = self._clamp_weight(weight)
        weight_file = group_path / "cpu.weight"

        try:
            weight_file.write_text(f"{clamped_w}\n", encoding="utf-8")
        except OSError as e:
            raise CgroupError(f"Failed to set cpu.weight on {group_path}: {e}") from e

    def move_pid(self, name: str, pid: int) -> None:
        """Migrate a single process ID into the group's cgroup.procs.

        Args:
            name: Group name.
            pid: Process ID.

        Raises:
            CgroupError: If PID migration fails.
        """
        group_path = self.base_cgroup_path / name
        procs_file = group_path / "cgroup.procs"

        try:
            with open(procs_file, "w", encoding="utf-8") as f:
                f.write(f"{pid}\n")
        except OSError as e:
            # Process may have already exited
            if e.errno == 3:  # ESRCH (No such process)
                logger.debug("Process %d exited before cgroup migration into %s", pid, name)
                return
            raise CgroupError(f"Failed to move PID {pid} to {group_path}: {e}") from e

    def move_pids(self, name: str, pids: Iterable[int]) -> None:
        """Migrate multiple process IDs into the group's cgroup.procs.

        Args:
            name: Group name.
            pids: Iterable of process IDs.
        """
        for pid in pids:
            self.move_pid(name, pid)

    def list_procs(self, name: str) -> List[int]:
        """List active process IDs currently in the cgroup.

        Args:
            name: Group name.

        Returns:
            List of integer PIDs.
        """
        group_path = self.base_cgroup_path / name
        procs_file = group_path / "cgroup.procs"
        if not procs_file.exists():
            return []

        try:
            content = procs_file.read_text(encoding="utf-8")
            pids = []
            for line in content.splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
            return pids
        except OSError as e:
            logger.debug("Could not read %s: %s", procs_file, e)
            return []

    def is_populated(self, name: str) -> bool:
        """Check cgroup.events to see if the cgroup contains any processes or threads.

        Args:
            name: Group name.

        Returns:
            True if populated==1, False if populated==0 or events file unreadable.
        """
        group_path = self.base_cgroup_path / name
        events_file = group_path / "cgroup.events"
        if not events_file.exists():
            return False

        try:
            content = events_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0] == "populated":
                    return parts[1] == "1"
            return False
        except OSError:
            return False

    def original_cgroup_of(self, pid: int) -> Optional[str]:
        """Determine the current cgroup path for a PID from /proc/<pid>/cgroup.

        Args:
            pid: Target process ID.

        Returns:
            Relative cgroup path (e.g. '/init.scope') or None if unreadable.
        """
        proc_cg_file = Path(f"/proc/{pid}/cgroup")
        try:
            content = proc_cg_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                parts = line.strip().split(":", 2)
                if len(parts) == 3:
                    return parts[2]
            return None
        except OSError:
            return None

    def remove_group(self, name: str) -> None:
        """Remove an empty cgroup directory via rmdir.

        Args:
            name: Group name.

        Raises:
            CgroupError: If removal fails.
        """
        group_path = self.base_cgroup_path / name
        if not group_path.exists():
            return

        try:
            os.rmdir(group_path)
        except OSError as e:
            raise CgroupError(f"Failed to remove cgroup {group_path}: {e}") from e

    def release_group(self, name: str, fallback_target: Optional[str] = None) -> None:
        """Release a cgroup: move any remaining PIDs to fallback target, then rmdir.

        Args:
            name: Group name.
            fallback_target: Target cgroup to move remaining processes into (default: root cgroup).
        """
        group_path = self.base_cgroup_path / name
        if not group_path.exists():
            return

        # Check for remaining processes
        remaining_pids = self.list_procs(name)
        if remaining_pids:
            target_procs = (
                self.base_cgroup_path / fallback_target.lstrip("/") / "cgroup.procs"
                if fallback_target
                else self.base_cgroup_path / "cgroup.procs"
            )
            for pid in remaining_pids:
                try:
                    with open(target_procs, "w", encoding="utf-8") as f:
                        f.write(f"{pid}\n")
                except OSError:
                    pass

        try:
            os.rmdir(group_path)
        except OSError as e:
            # If still busy, log debug
            logger.debug("Could not rmdir %s during release: %s", group_path, e)

    def list_stale_groups(self, prefix: str = "precompile-res-") -> List[str]:
        """List any leftover reservation directories matching the prefix.

        Args:
            prefix: Prefix to match (default 'precompile-res-').

        Returns:
            List of group names.
        """
        if not self.base_cgroup_path.exists():
            return []

        stale: List[str] = []
        try:
            for entry in os.listdir(self.base_cgroup_path):
                if entry.startswith(prefix) and (self.base_cgroup_path / entry).is_dir():
                    stale.append(entry)
        except OSError as e:
            logger.warning("Error reading %s for stale groups: %s", self.base_cgroup_path, e)
        return stale

    def cleanup_stale(self, prefix: str = "precompile-res-") -> int:
        """Release and remove all stale reservation groups.

        Args:
            prefix: Prefix to match (default 'precompile-res-').

        Returns:
            Number of groups successfully cleaned up.
        """
        stale = self.list_stale_groups(prefix)
        cleaned = 0
        for name in stale:
            try:
                self.release_group(name)
                cleaned += 1
            except Exception as e:
                logger.warning("Failed to clean up stale group %s: %s", name, e)
        return cleaned

    # Backward-compatibility skeleton aliases
    def prepare_reservation_cgroup(self, reservation_id: str, elevated_weight: int = 500) -> Path:
        """Create empty reservation cgroup with elevated weight."""
        return self.create_group(f"precompile-res-{reservation_id}", elevated_weight)

    def move_process_to_cgroup(self, cgroup_path: Path, pid: int) -> None:
        """Migrate single PID to target cgroup."""
        self.move_pid(cgroup_path.name, pid)

    def move_process_tree(self, cgroup_path: Path, pids: Set[int]) -> None:
        """Migrate set of PIDs to target cgroup."""
        self.move_pids(cgroup_path.name, pids)

    def is_cgroup_empty(self, cgroup_path: Path) -> bool:
        """Check if cgroup is empty (populated == 0)."""
        return not self.is_populated(cgroup_path.name)

    def release_cgroup(self, cgroup_path: Path) -> None:
        """Release and remove cgroup."""
        self.release_group(cgroup_path.name)


class FakeCgroupBackend:
    """In-memory mock cgroup backend for unit tests without root privileges."""

    def __init__(
        self,
        weight_cap: int = 500,
        default_weight: int = 500,
        base_cgroup_path: Path = Path("/fake/sys/fs/cgroup"),
    ) -> None:
        self.weight_cap = weight_cap
        self.default_weight = default_weight
        self.base_cgroup_path = base_cgroup_path
        self.groups: Dict[str, Dict[str, Any]] = {}
        self.proc_cgroup_map: Dict[int, str] = {}
        self.cpu_controller_enabled = False

    def _clamp_weight(self, weight: Optional[int]) -> int:
        w = weight if weight is not None else self.default_weight
        return max(1, min(int(w), self.weight_cap))

    def ensure_cpu_controller(self) -> None:
        self.cpu_controller_enabled = True

    def create_group(self, name: str, weight: Optional[int] = None) -> Path:
        clamped_w = self._clamp_weight(weight)
        self.groups[name] = {
            "weight": clamped_w,
            "pids": set(),
            "populated": False,
        }
        return self.base_cgroup_path / name

    def set_weight(self, name: str, weight: int) -> None:
        if name not in self.groups:
            raise CgroupError(f"Group {name} does not exist")
        self.groups[name]["weight"] = self._clamp_weight(weight)

    def move_pid(self, name: str, pid: int) -> None:
        if name not in self.groups:
            raise CgroupError(f"Group {name} does not exist")
        # Remove from other groups
        for g_data in self.groups.values():
            g_data["pids"].discard(pid)
            g_data["populated"] = bool(g_data["pids"])
        self.groups[name]["pids"].add(pid)
        self.groups[name]["populated"] = True
        self.proc_cgroup_map[pid] = f"/{name}"

    def move_pids(self, name: str, pids: Iterable[int]) -> None:
        for pid in pids:
            self.move_pid(name, pid)

    def list_procs(self, name: str) -> List[int]:
        if name not in self.groups:
            return []
        return sorted(list(self.groups[name]["pids"]))

    def is_populated(self, name: str) -> bool:
        if name not in self.groups:
            return False
        return self.groups[name]["populated"]

    def original_cgroup_of(self, pid: int) -> Optional[str]:
        return self.proc_cgroup_map.get(pid, "/init.scope")

    def remove_group(self, name: str) -> None:
        if name in self.groups:
            if self.groups[name]["pids"]:
                raise CgroupError(f"Cannot remove populated group {name}")
            del self.groups[name]

    def release_group(self, name: str, fallback_target: Optional[str] = None) -> None:
        if name in self.groups:
            pids = list(self.groups[name]["pids"])
            target = fallback_target or ""
            for pid in pids:
                self.proc_cgroup_map[pid] = target
            del self.groups[name]

    def list_stale_groups(self, prefix: str = "precompile-res-") -> List[str]:
        return [g for g in self.groups if g.startswith(prefix)]

    def cleanup_stale(self, prefix: str = "precompile-res-") -> int:
        stale = self.list_stale_groups(prefix)
        for s in stale:
            self.release_group(s)
        return len(stale)
