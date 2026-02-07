# src/betterci/dsl.py
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Iterable, List, Optional, Dict, Sequence, Union

from .model import Step, Job


# ---------------------------------------------------------------------
# Step helper
# ---------------------------------------------------------------------

def sh(name: str, cmd: str, *, cwd: str | None = None) -> Step:
    """Create a shell step."""
    return Step(name=name, run=cmd, cwd=cwd)


# ---------------------------------------------------------------------
# Functional Job helper (nice DX)
# ---------------------------------------------------------------------

def job(
    name: str,
    *steps: Step,  # allow: job("x", sh(...), sh(...))
    steps_list: Optional[List[Step]] = None,  # allow: job("x", steps_list=[...])
    needs: Optional[List[str]] = None,
    inputs: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    requires: Optional[List[str]] = None,
    paths: Optional[List[str]] = None,
    diff_enabled: bool = True,
    cwd: str | None = None,  # default cwd applied to steps missing cwd
    # cache knobs (will be attached if your Job supports them)
    cache_dirs: Optional[List[str]] = None,
    cache_enabled: bool = True,
    cache_skip_on_hit: bool = False,
    cache_keep: int = 3,
) -> Job:
    steps_final: List[Step] = []
    if steps_list:
        steps_final.extend(list(steps_list))
    steps_final.extend(list(steps))

    if not steps_final:
        raise ValueError(f"job({name!r}) must have at least one step")

    if cwd is not None:
        steps_final = [s if s.cwd is not None else replace(s, cwd=cwd) for s in steps_final]

    j = Job(
        name=name,
        steps=steps_final,
        inputs=inputs or [],
        env=env or {},
        requires=requires or [],
        paths=paths,
        diff_enabled=diff_enabled,
        # dependency field compatibility handled below
    )

    # --- dependency field compatibility (needs vs dependency) ---
    if hasattr(j, "needs"):
        setattr(j, "needs", needs or [])
    else:
        setattr(j, "dependency", needs or [])

    # --- cache field compatibility (only set if Job has these attrs) ---
    if cache_dirs is not None and hasattr(j, "cache_dirs"):
        setattr(j, "cache_dirs", cache_dirs)
    if hasattr(j, "cache_enabled"):
        setattr(j, "cache_enabled", cache_enabled)
    if hasattr(j, "cache_skip_on_hit"):
        setattr(j, "cache_skip_on_hit", cache_skip_on_hit)
    if hasattr(j, "cache_keep"):
        setattr(j, "cache_keep", cache_keep)

    return j


# ---------------------------------------------------------------------
# Builder API (your current style)
# ---------------------------------------------------------------------

class JobBuilder:
    def __init__(self, name: str):
        self.name = name
        self._needs: list[str] = []
        self._steps: list[Step] = []
        self._inputs: list[str] = []
        self._env: dict[str, str] = {}
        self._requires: list[str] = []
        self._paths: Optional[list[str]] = None
        self._diff_enabled: bool = True

        # cache knobs (optional)
        self._cache_dirs: Optional[list[str]] = None
        self._cache_enabled: bool = True
        self._cache_skip_on_hit: bool = False
        self._cache_keep: int = 3

    def depends_on(self, *job_names: str):
        self._needs.extend(job_names)
        return self

    def define_requirements(self, *tools: str):
        self._requires.extend(tools)
        return self

    # Back-compat alias for your misspelling
    def define_requirments(self, *tools: str):
        return self.define_requirements(*tools)

    def define_step(self, name: str, run: str, cwd: str | None = None):
        self._steps.append(Step(name=name, run=run, cwd=cwd))
        return self

    def with_inputs(self, *paths: str):
        self._inputs.extend(paths)
        return self

    def with_env(self, **env):
        # force values to str for stable hashing + env compatibility
        self._env.update({k: str(v) for k, v in env.items()})
        return self

    def with_paths(self, *patterns: str):
        self._paths = list(patterns)
        return self

    def enable_diff(self, enabled: bool = True):
        self._diff_enabled = enabled
        return self

    # cache sugar (optional)
    def cache_dirs(self, *dirs: str):
        self._cache_dirs = list(dirs)
        return self

    def cache_behavior(self, *, enabled: bool = True, skip_on_hit: bool = False, keep: int = 3):
        self._cache_enabled = enabled
        self._cache_skip_on_hit = skip_on_hit
        self._cache_keep = keep
        return self

    def build(self) -> Job:
        if not self._steps:
            raise ValueError(f"Job '{self.name}' has no steps")

        j = Job(
            name=self.name,
            steps=self._steps,
            inputs=self._inputs,
            env=self._env,
            requires=self._requires,
            paths=self._paths,
            diff_enabled=self._diff_enabled,
        )

        # deps compatibility
        if hasattr(j, "needs"):
            setattr(j, "needs", self._needs)
        else:
            setattr(j, "dependency", self._needs)

        # cache compatibility
        if self._cache_dirs is not None and hasattr(j, "cache_dirs"):
            setattr(j, "cache_dirs", self._cache_dirs)
        if hasattr(j, "cache_enabled"):
            setattr(j, "cache_enabled", self._cache_enabled)
        if hasattr(j, "cache_skip_on_hit"):
            setattr(j, "cache_skip_on_hit", self._cache_skip_on_hit)
        if hasattr(j, "cache_keep"):
            setattr(j, "cache_keep", self._cache_keep)

        return j


def build(name: str) -> JobBuilder:
    """Convenience: build('test').define_step(...).build()"""
    return JobBuilder(name)


# ---------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------

class Matrix:
    """
    Minimal matrix expander.

    Example:
        matrix("py", ["3.10","3.11"]).jobs(
            lambda v: job(f"test-py{v}", sh(...))
        )
    """
    def __init__(self, key: str, values: Iterable[Any]):
        self.key = key
        self.values = list(values)

    def jobs(self, builder: Callable[[Any], Job]) -> List[Job]:
        return [builder(v) for v in self.values]


def matrix(key: str, values: Iterable[Any]) -> Matrix:
    return Matrix(key, values)


# ---------------------------------------------------------------------
# Workflow helper (single-file story)
# ---------------------------------------------------------------------

def wf(*jobs: Job) -> List[Job]:
    """
    Workflow definition helper. Use this name so you can define your own
    def workflow(): return wf(job(...), job(...)).

    Users can write:
        from betterci import wf, job, sh

        def workflow():
            return wf(
                job(...),
                job(...),
            )

    Or use JOBS directly:
        JOBS = wf(job(...), job(...))
    """
    return list(jobs)


workflow = wf  # backward-compat alias (avoid naming your function workflow if you use it)
