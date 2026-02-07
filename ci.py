# dsl.py
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Iterable, List, Optional, Dict, Sequence, Union

from .model import Job, Step  # <-- fix import


def sh(name: str, cmd: str, *, cwd: str | None = None) -> Step:
    return Step(name=name, run=cmd, cwd=cwd)


def job(
    name: str,
    *steps: Step,  # allow job("x", sh(...), sh(...))
    steps_list: Optional[List[Step]] = None,  # still allow job(..., steps_list=[...])
    needs: Optional[List[str]] = None,
    inputs: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    requires: Optional[List[str]] = None,
    paths: Optional[List[str]] = None,
    diff_enabled: bool = True,
    # caching knobs (future-proof, matches cache.py)
    cache_enabled: bool = True,
    cache_dirs: Optional[List[str]] = None,
    cache_exclude: Optional[List[str]] = None,
    cache_key_extra: Optional[Dict[str, str]] = None,
    # convenience
    cwd: str | None = None,  # default cwd for steps
) -> Job:
    if steps_list is None:
        steps_list = []
    steps_final = list(steps_list) + list(steps)

    if not steps_final:
        raise ValueError(f"job({name!r}) must have at least one step")

    if cwd is not None:
        steps_final = [
            s if s.cwd is not None else replace(s, cwd=cwd)
            for s in steps_final
        ]

    return Job(
        name=name,
        steps=steps_final,
        # IMPORTANT: make your model field match this (prefer `needs`)
        needs = needs or [],
        inputs=inputs or [],
        env=env or {},
        requires=requires or [],
        paths=paths,
        diff_enabled=diff_enabled,
        # These only work if you add them to Job dataclass (recommended)
        # cache_enabled=cache_enabled,
        # cache_dirs=cache_dirs or [],
        # cache_exclude=cache_exclude or [],
        # cache_key_extra=cache_key_extra or {},
    )


class Matrix:
    def __init__(self, key: str, values: Iterable[Any]):
        self.key = key
        self.values = list(values)

    def jobs(self, builder: Callable[[Any], Job]) -> List[Job]:
        return [builder(v) for v in self.values]


def matrix(key: str, values: Iterable[Any]) -> Matrix:
    return Matrix(key, values)
