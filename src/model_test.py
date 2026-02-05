# model.py (test version)
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass(frozen=True)
class Step:
    name: str
    run: str
    cwd: Optional[str] = None


@dataclass(frozen=True)
class Job:
    name: str
    steps: List[Step]
    needs: List[str]              # DAG dependencies (job names)
    requires: List[str] | None = None  # tools (optional)
    env: Dict[str, str] | None = None
