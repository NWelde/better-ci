# model.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Dict


@dataclass(frozen=True)
class Step:
    """A single command (step) inside a CI job."""
    name: str
    run: str
    cwd: str | None = None


@dataclass
class Job:
    """
    A CI job: steps + dependencies + metadata for selection/caching.

    Canonical dependency field: `needs`
    Backwards-compatible alias: `dependency`
    """
    name: str
    steps: list[Step]

    # Canonical name used by DAG / planner / DSL (recommended)
    needs: list[str] = field(default_factory=list)

    inputs: list[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    requires: list[str] = field(default_factory=list)

    # Git diff based selection
    paths: Optional[List[str]] = None          # e.g. ["backend/**", "shared/**"]
    diff_enabled: bool = True                  # per-job opt-out

    # ---- Backwards-compatible alias ----
    @property
    def dependency(self) -> list[str]:
        # old name -> new canonical field
        return self.needs

    @dependency.setter
    def dependency(self, value: list[str]) -> None:
        self.needs = value
