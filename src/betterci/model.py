# model.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass(frozen=True)
class Step:
    """A single command (step) inside a CI job."""
    name: str
    run: str = ""
    cwd: str | None = None
    # Typed steps (e.g. kind="test") are expanded into shell steps before execution.
    kind: Optional[str] = None
    data: Optional[Dict] = None
    # Routes to step_workflows/<workflow_type>.py run_step() handler.
    # Used by lint_step() and docker_step() helpers.
    workflow_type: Optional[str] = None
    # Step-type-specific metadata (replaces object.__setattr__ hacks).
    # lint_step stores {"tool": ..., "args": ..., "files": ...}
    # docker_step stores {"image": ..., "volumes": ..., "env": ..., "user": ...}
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Job:
    """
    A CI job: steps + dependencies + metadata for selection/caching.

    Canonical dependency field: `needs`
    Backwards-compatible alias: `dependency`
    """
    name: str
    steps: list[Step]

    # Dependency ordering
    needs: list[str] = field(default_factory=list)

    # Input hashing for cache key
    inputs: list[str] = field(default_factory=list)

    # Environment variables injected into every step
    env: Dict[str, str] = field(default_factory=dict)

    # Required tools (checked before job runs via shutil.which)
    requires: list[str] = field(default_factory=list)

    # Required environment variables / secrets (checked before job runs)
    # Fails fast with a clear error if any are missing — no more 3-hour surprises.
    secrets: list[str] = field(default_factory=list)

    # Git diff based selection
    paths: Optional[List[str]] = None     # e.g. ["backend/**", "shared/**"]
    diff_enabled: bool = True             # per-job opt-out of git-diff filtering

    # ---- Cache configuration (first-class fields, no more getattr hacks) ----
    cache_dirs: List[str] = field(default_factory=list)
    cache_enabled: bool = True
    cache_skip_on_hit: bool = False
    cache_keep: int = 3

    # ---- Backwards-compatible alias ----
    @property
    def dependency(self) -> list[str]:
        return self.needs

    @dependency.setter
    def dependency(self, value: list[str]) -> None:
        self.needs = value
