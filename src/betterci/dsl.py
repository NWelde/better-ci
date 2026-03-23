# src/betterci/dsl.py
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Iterable, List, Optional, Dict, Sequence, Union, Literal

from .model import Step, Job


# ---------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------

def sh(name: str, cmd: str, *, cwd: str | None = None) -> Step:
    """Create a shell step."""
    return Step(name=name, run=cmd, cwd=cwd)


def test(
    name: str,
    *,
    framework: Literal["pytest", "npm"],
    args: str = "",
    install: bool = True,
    cwd: str | None = None,
) -> Step:
    """
    Typed test step. Expanded deterministically into shell commands before execution.

    Example:
        test("Run tests", framework="pytest", args="-q")
        test("JS tests", framework="npm", install=True)
    """
    return Step(
        name=name,
        kind="test",
        cwd=cwd,
        data={
            "framework": framework,
            "args": args,
            "install": install,
        },
    )


def lint_step(
    name: str,
    tool: str,
    args: str | None = None,
    *,
    cwd: str | None = None,
    files: List[str] | None = None,
) -> Step:
    """
    Create a lint step.

    Example:
        lint_step("Ruff", "ruff", "check src/")
        lint_step("ESLint", "eslint", files=["src/"])
    """
    cmd = tool
    if args:
        cmd = f"{tool} {args}"
    return Step(
        name=name,
        run=cmd,
        cwd=cwd,
        workflow_type="lint",
        meta={
            "tool": tool,
            "args": args or "",
            "files": files or [],
        },
    )


def docker_step(
    name: str,
    cmd: str,
    image: str,
    *,
    cwd: str | None = None,
    volumes: List[str] | None = None,
    env: Dict[str, str] | None = None,
    user: str | None = None,
) -> Step:
    """
    Create a step that runs inside a Docker container.

    The repo root is mounted as /workspace inside the container.

    Example:
        docker_step("Build", "make build", image="python:3.11-slim")
        docker_step("Test", "pytest", image="myimage", volumes=["~/.cache:/cache"])
    """
    return Step(
        name=name,
        run=cmd,
        cwd=cwd,
        workflow_type="docker",
        meta={
            "image": image,
            "volumes": volumes or [],
            "env": env or {},
            "user": user or "",
        },
    )


# ---------------------------------------------------------------------
# Job helper (functional API)
# ---------------------------------------------------------------------

def job(
    name: str,
    *steps: Step,
    steps_list: Optional[List[Step]] = None,
    needs: Optional[List[str]] = None,
    inputs: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    requires: Optional[List[str]] = None,
    secrets: Optional[List[str]] = None,
    paths: Optional[List[str]] = None,
    diff_enabled: bool = True,
    cwd: str | None = None,
    # Cache configuration
    cache_dirs: Optional[List[str]] = None,
    cache_enabled: bool = True,
    cache_skip_on_hit: bool = False,
    cache_keep: int = 3,
) -> Job:
    """
    Define a CI job.

    Args:
        name:              Unique job name.
        *steps:            Steps to run (created with sh(), test(), lint_step(), etc.).
        steps_list:        Alternative: pass steps as a list.
        needs:             Job names that must complete before this job starts.
        inputs:            File/glob patterns whose contents are hashed into the cache key.
        env:               Environment variables injected into every step.
        requires:          Tools that must be installed before this job runs (e.g. ["npm", "docker"]).
                           BetterCI checks these with shutil.which() before executing any steps.
        secrets:           Required environment variable names (e.g. ["API_KEY", "DATABASE_URL"]).
                           BetterCI validates these are set BEFORE running the job — no more
                           wasting hours before hitting a missing secret.
        paths:             Glob patterns for git-diff selection. With --git-diff, this job only
                           runs if one of these files changed.
        diff_enabled:      Set False to always run this job, even with --git-diff.
        cwd:               Default working directory for steps that don't specify their own.
        cache_dirs:        Directories to save/restore between runs (e.g. [".venv", "node_modules"]).
        cache_enabled:     Set False to disable caching for this job.
        cache_skip_on_hit: Set True to skip running steps entirely on a cache hit.
        cache_keep:        Number of cache archives to keep (oldest pruned automatically).
    """
    steps_final: List[Step] = []
    if steps_list:
        steps_final.extend(steps_list)
    steps_final.extend(list(steps))

    if not steps_final:
        raise ValueError(f"job({name!r}) must have at least one step")

    if cwd is not None:
        steps_final = [s if s.cwd is not None else replace(s, cwd=cwd) for s in steps_final]

    return Job(
        name=name,
        steps=steps_final,
        needs=needs or [],
        inputs=inputs or [],
        env=env or {},
        requires=requires or [],
        secrets=secrets or [],
        paths=paths,
        diff_enabled=diff_enabled,
        cache_dirs=cache_dirs or [],
        cache_enabled=cache_enabled,
        cache_skip_on_hit=cache_skip_on_hit,
        cache_keep=cache_keep,
    )


# ---------------------------------------------------------------------
# Builder API (fluent / chainable)
# ---------------------------------------------------------------------

class JobBuilder:
    """
    Fluent builder for CI jobs. Alternative to the functional job() API.

    Example:
        build("test")
            .depends_on("lint")
            .define_step("install", "pip install -e .")
            .define_step("test", "pytest -q")
            .with_inputs("pyproject.toml")
            .cache_dirs(".venv")
            .requires_secrets("API_KEY")
            .build()
    """

    def __init__(self, name: str):
        self.name = name
        self._needs: list[str] = []
        self._steps: list[Step] = []
        self._inputs: list[str] = []
        self._env: dict[str, str] = {}
        self._requires: list[str] = []
        self._secrets: list[str] = []
        self._paths: Optional[list[str]] = None
        self._diff_enabled: bool = True
        self._cache_dirs: list[str] = []
        self._cache_enabled: bool = True
        self._cache_skip_on_hit: bool = False
        self._cache_keep: int = 3

    def depends_on(self, *job_names: str) -> "JobBuilder":
        self._needs.extend(job_names)
        return self

    def define_requirements(self, *tools: str) -> "JobBuilder":
        self._requires.extend(tools)
        return self

    # Back-compat alias
    def define_requirments(self, *tools: str) -> "JobBuilder":
        return self.define_requirements(*tools)

    def requires_secrets(self, *env_vars: str) -> "JobBuilder":
        """Declare required environment variables / secrets."""
        self._secrets.extend(env_vars)
        return self

    def define_step(self, name: str, run: str, cwd: str | None = None) -> "JobBuilder":
        self._steps.append(Step(name=name, run=run, cwd=cwd))
        return self

    def add_step(self, step: Step) -> "JobBuilder":
        self._steps.append(step)
        return self

    def with_inputs(self, *paths: str) -> "JobBuilder":
        self._inputs.extend(paths)
        return self

    def with_env(self, **env) -> "JobBuilder":
        self._env.update({k: str(v) for k, v in env.items()})
        return self

    def with_paths(self, *patterns: str) -> "JobBuilder":
        self._paths = list(patterns)
        return self

    def enable_diff(self, enabled: bool = True) -> "JobBuilder":
        self._diff_enabled = enabled
        return self

    def cache_dirs(self, *dirs: str) -> "JobBuilder":
        self._cache_dirs = list(dirs)
        return self

    def cache_behavior(
        self, *, enabled: bool = True, skip_on_hit: bool = False, keep: int = 3
    ) -> "JobBuilder":
        self._cache_enabled = enabled
        self._cache_skip_on_hit = skip_on_hit
        self._cache_keep = keep
        return self

    def build(self) -> Job:
        if not self._steps:
            raise ValueError(f"Job '{self.name}' has no steps")

        return Job(
            name=self.name,
            steps=self._steps,
            needs=self._needs,
            inputs=self._inputs,
            env=self._env,
            requires=self._requires,
            secrets=self._secrets,
            paths=self._paths,
            diff_enabled=self._diff_enabled,
            cache_dirs=self._cache_dirs,
            cache_enabled=self._cache_enabled,
            cache_skip_on_hit=self._cache_skip_on_hit,
            cache_keep=self._cache_keep,
        )


def build(name: str) -> JobBuilder:
    """Start a fluent job builder: build('test').define_step(...).build()"""
    return JobBuilder(name)


# ---------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------

class Matrix:
    """
    Generate jobs across multiple values.

    Example:
        matrix("py", ["3.10", "3.11", "3.12"]).jobs(
            lambda v: job(f"test-{v}", sh("test", f"python{v} -m pytest"))
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
# Workflow collector
# ---------------------------------------------------------------------

def wf(*jobs: Job) -> List[Job]:
    """
    Collect jobs into a workflow list.

    Usage:
        from betterci import wf, job, sh

        def workflow():
            return wf(
                job("lint",  sh("lint", "ruff check src/")),
                job("test",  sh("test", "pytest -q"), needs=["lint"]),
            )
    """
    return list(jobs)


workflow = wf  # backward-compat alias
