# dsl.py
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Iterable, List, Optional, Dict

from .model import Step, Job


# ----------------------------
# Simple helpers (nice DX)
# ----------------------------

def sh(name: str, cmd: str, *, cwd: str | None = None) -> Step:
    """Create a shell step."""
    return Step(name=name, run=cmd, cwd=cwd)


def job(
    name: str,
    *steps: Step,                      # allow: job("test", sh(...), sh(...))
    steps_list: Optional[List[Step]] = None,  # allow: job("test", steps_list=[...])
    needs: Optional[List[str]] = None,
    inputs: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    requires: Optional[List[str]] = None,
    paths: Optional[List[str]] = None,
    diff_enabled: bool = True,
    # optional cache knobs (if/when you add to Job model)
    cache_dirs: Optional[List[str]] = None,
    cache_enabled: bool = True,
    cache_skip_on_hit: bool = False,
    cache_keep: int = 3,
    cwd: str | None = None,            # default cwd applied to steps missing cwd
) -> Job:
    steps_final: List[Step] = []
    if steps_list:
        steps_final.extend(list(steps_list))
    steps_final.extend(list(steps))

    if not steps_final:
        raise ValueError(f"job({name!r}) must have at least one step")

    if cwd is not None:
        steps_final = [s if s.cwd is not None else replace(s, cwd=cwd) for s in steps_final]

    # IMPORTANT: keep this aligned with your model.py canonical dep field.
    # If you migrated model.py to `needs`, use needs=...
    # If your model still stores `dependency`, keep dependency=...
    return Job(
        name=name,
        steps=steps_final,
        needs=needs or [],              # <-- if your Job has needs
        # dependency=needs or [],       # <-- if your Job still uses dependency
        inputs=inputs or [],
        env=env or {},
        requires=requires or [],
        paths=paths,
        diff_enabled=diff_enabled,
        # These work only if you add these fields to Job dataclass:
        # cache_dirs=cache_dirs or [],
        # cache_enabled=cache_enabled,
        # cache_skip_on_hit=cache_skip_on_hit,
        # cache_keep=cache_keep,
    )


# ----------------------------
# Builder style (your current API)
# ----------------------------

class JobBuilder:
    def __init__(self, name: str):
        self.name = name
        self.needs: list[str] = []          # rename for consistency
        self.steps: list[Step] = []
        self.inputs: list[str] = []
        self.env: dict[str, str] = {}
        self.requires: list[str] = []
        self.paths: list[str] | None = None
        self.diff_enabled: bool = True

    def depends_on(self, *job_names: str):
        self.needs.extend(job_names)
        return self

    def define_requirements(self, *tools: str):
        self.requires.extend(tools)
        return self

    def define_step(self, name: str, run: str, cwd: str | None = None):
        self.steps.append(Step(name=name, run=run, cwd=cwd))
        return self

    def with_inputs(self, *paths: str):
        self.inputs.extend(paths)
        return self

    def with_env(self, **env):
        self.env.update({k: str(v) for k, v in env.items()})
        return self

    def with_paths(self, *patterns: str):
        self.paths = list(patterns)
        return self

    def enable_diff(self, enabled: bool = True):
        self.diff_enabled = enabled
        return self

    def build(self) -> Job:
        if not self.steps:
            raise ValueError(f"Job '{self.name}' has no steps")

        return Job(
            name=self.name,
            steps=self.steps,
            needs=self.needs,               # <-- if your model uses needs
            # dependency=self.needs,        # <-- if your model still uses dependency
            inputs=self.inputs,
            env=self.env,
            requires=self.requires,
            paths=self.paths,
            diff_enabled=self.diff_enabled,
        )


# ----------------------------
# Matrix (optional but nice)
# ----------------------------

class Matrix:
    def __init__(self, key: str, values: Iterable[Any]):
        self.key = key
        self.values = list(values)

    def jobs(self, builder: Callable[[Any], Job]) -> List[Job]:
        return [builder(v) for v in self.values]


def matrix(key: str, values: Iterable[Any]) -> Matrix:
    return Matrix(key, values)
