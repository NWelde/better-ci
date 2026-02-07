# model.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class Step:
    name: str
    run: str | None = None        # optional for typed steps
    cwd: str | None = None
    kind: str = "sh"              # "sh", "test", "docker", etc.
    data: dict[str, Any] = field(default_factory=dict)  # structured payload
