# runner.py
from __future__ import annotations

import ast
import os
import runpy
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .model import Job, Step
from .cache import CacheStore, CacheHit
from .git_facts.git import (
    repo_root,
    head_sha,
    is_dirty,
    merge_base,
    changed_files as changed_files_between,
)
from .ui.console import get_console

# local dev ---> commit ---> CI ---> push ---> cloud CI


# ---------------------------------------------------------------------------
# Structured errors
# ---------------------------------------------------------------------------

@dataclass
class CIError(Exception):
    """
    Structured CI error with enough context for clean CLI output and debugging.
    """
    kind: str
    job: str
    step: str | None
    message: str
    details: dict

    def __str__(self) -> str:
        lines = [f"{self.kind}: {self.message}", f"job={self.job}"]
        if self.step:
            lines.append(f"step={self.step}")
        for k, v in self.details.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)


@dataclass
class StepFailure(Exception):
    job: str
    step: str
    cmd: str
    exit_code: int

    def __str__(self) -> str:
        return (
            f"[{self.job}] step '{self.step}' failed "
            f"(exit={self.exit_code}): {self.cmd}"
        )


# ---------------------------------------------------------------------------
# Tool hints (used in error messages)
# ---------------------------------------------------------------------------

TOOL_HINTS = {
    "npm":     "Install Node.js (includes npm) or fix PATH.",
    "node":    "Install Node.js or fix PATH.",
    "pytest":  "Install pytest: pip install pytest",
    "ruff":    "Install ruff: pip install ruff",
    "docker":  "Install Docker and ensure the daemon is running.",
    "python3": "Install Python 3 or fix PATH.",
    "pip":     "Install pip or use a virtual environment.",
    "cargo":   "Install Rust via rustup.rs",
    "go":      "Install Go from https://go.dev/dl/",
}


# ---------------------------------------------------------------------------
# Constrained workflow loading
# ---------------------------------------------------------------------------

# Allowed top-level imports in workflow files.
# Everything else triggers a warning (or error in --safe mode).
_ALLOWED_IMPORT_PREFIXES = ("betterci",)


def _audit_workflow_imports(wf_path: Path) -> List[str]:
    """
    Parse the workflow file's AST and return a list of warnings for any
    imports that aren't from the betterci package.

    BetterCI's constrained execution model: workflow files should be a
    description layer only — they declare jobs, not execute logic.
    Importing arbitrary modules at load time breaks that contract.
    """
    warnings: List[str] = []
    try:
        source = wf_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(wf_path))
    except SyntaxError as e:
        warnings.append(f"Syntax error in workflow file: {e}")
        return warnings
    except Exception:
        return warnings

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not any(alias.name.startswith(p) for p in _ALLOWED_IMPORT_PREFIXES):
                    warnings.append(
                        f"line {node.lineno}: non-betterci import: "
                        f"'import {alias.name}'"
                    )
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if not any(mod.startswith(p) for p in _ALLOWED_IMPORT_PREFIXES):
                warnings.append(
                    f"line {node.lineno}: non-betterci import: "
                    f"'from {mod} import ...'"
                )

    return warnings


def load_workflow(path: str | Path, *, safe: bool = False) -> List[Job]:
    """
    Load a workflow from a Python file.

    The file must define either:
      - workflow() -> List[Job]
      - JOBS = [Job, ...]

    Args:
        path: Path to the .py workflow file.
        safe: If True, raise an error if the workflow file imports anything
              outside the betterci package (constrained execution model).
              If False, non-betterci imports trigger a warning only.

    Returns:
        List[Job]
    """
    wf_path = Path(path).expanduser().resolve()
    if not wf_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {wf_path}")
    if wf_path.suffix != ".py":
        raise ValueError(f"Workflow must be a .py file, got: {wf_path.name}")

    # ------------------------------------------------------------------
    # Constrained execution model: audit imports before loading
    # ------------------------------------------------------------------
    import_warnings = _audit_workflow_imports(wf_path)
    console = get_console()
    if import_warnings:
        if safe:
            raise CIError(
                kind="unsafe_workflow",
                job="<load>",
                step=None,
                message=(
                    "Workflow file imports modules outside of betterci. "
                    "In --safe mode, workflow files must only import from betterci. "
                    "This enforces the constrained execution model: workflow files "
                    "describe jobs, they do not execute arbitrary Python."
                ),
                details={f"warning_{i}": w for i, w in enumerate(import_warnings)},
            )
        else:
            for w in import_warnings:
                console.print_warning(
                    f"Workflow import warning: {w}\n"
                    "  Workflow files should only import from betterci. "
                    "Use --safe to enforce this."
                )

    module_name = f"betterci_workflow_{wf_path.stem}"
    globals_dict = runpy.run_path(str(wf_path), run_name=module_name)

    jobs = None
    if "workflow" in globals_dict and callable(globals_dict["workflow"]):
        try:
            jobs = globals_dict["workflow"]()
        except TypeError as e:
            if "positional arguments but" in str(e) and "was given" in str(e):
                raise TypeError(
                    "Your workflow() is being called with arguments — likely a name collision "
                    "with the betterci wf() helper. Use: "
                    "`from betterci import wf, job, sh` then "
                    "`def workflow(): return wf(job(...), job(...))`"
                ) from e
            raise
    elif "JOBS" in globals_dict:
        jobs = globals_dict["JOBS"]

    if not isinstance(jobs, list) or not all(isinstance(j, Job) for j in jobs):
        raise TypeError(
            "Workflow must return/define a List[Job]. "
            "Define `def workflow() -> List[Job]` or `JOBS = [Job, ...]`."
        )

    return jobs


# ---------------------------------------------------------------------------
# Pre-flight checks (run before any step executes)
# ---------------------------------------------------------------------------

def _preflight_tools(job: Job) -> List[str]:
    """
    Check that all tools in job.requires are available on PATH.
    Returns a list of missing tool names.
    """
    missing = []
    for tool in (job.requires or []):
        if shutil.which(tool) is None:
            missing.append(tool)
    return missing


def _preflight_secrets(job: Job) -> List[str]:
    """
    Check that all required env vars in job.secrets are present.
    Checks both os.environ and the job's own env dict.
    Returns a list of missing variable names.
    """
    missing = []
    for secret in (job.secrets or []):
        if secret not in os.environ and secret not in (job.env or {}):
            missing.append(secret)
    return missing


def _run_preflight(job: Job) -> None:
    """
    Run all pre-flight checks and raise CIError immediately if anything is missing.

    This is the "failed secret validation shouldn't cost you three hours" promise:
    we validate everything up front before a single step executes.
    """
    # Tool check
    missing_tools = _preflight_tools(job)
    if missing_tools:
        hints = {t: TOOL_HINTS.get(t, f"Install {t!r} or fix PATH.") for t in missing_tools}
        raise CIError(
            kind="missing_tools",
            job=job.name,
            step=None,
            message=f"Required tool(s) not found: {', '.join(missing_tools)}",
            details=hints,
        )

    # Secret / env var check
    missing_secrets = _preflight_secrets(job)
    if missing_secrets:
        raise CIError(
            kind="missing_secrets",
            job=job.name,
            step=None,
            message=(
                f"Required environment variable(s) not set: {', '.join(missing_secrets)}\n"
                f"  Declare them in job(secrets=[...]) to get this check upfront."
            ),
            details={s: "not set in environment" for s in missing_secrets},
        )


# ---------------------------------------------------------------------------
# Test step expansion
# ---------------------------------------------------------------------------

def _expand_steps(job: Job) -> List[Step]:
    """
    Expand typed steps (kind='test') into concrete shell steps.
    Regular steps and workflow_type steps are passed through unchanged.

    This is called once per job before execution begins — the runner
    never sees kind='test' steps at execution time.
    """
    from .step_workflows.test import compile_test

    expanded: List[Step] = []
    for step in job.steps:
        if step.kind == "test":
            expanded.extend(compile_test(step))
        else:
            expanded.append(step)
    return expanded


# ---------------------------------------------------------------------------
# Git diff utilities
# ---------------------------------------------------------------------------

def git_functionality(
    compare_ref: str = "origin/main",
) -> Tuple[Optional[str], List[str]]:
    """
    Returns (recent_commit_head, changed_files).

    recent_commit_head:
      - full SHA for HEAD if repo is clean
      - None if repo has uncommitted changes (dirty)
    changed_files:
      - list of changed file paths relative to repo root
    """
    root: Path = repo_root()
    original_cwd = os.getcwd()

    try:
        os.chdir(root)

        dirty = is_dirty()
        recent_commit_head: Optional[str] = None if dirty else head_sha()

        if dirty:
            files: Set[str] = set()

            unstaged = subprocess.check_output(
                ["git", "diff", "--name-only"], cwd=root, text=True
            ).strip()
            staged = subprocess.check_output(
                ["git", "diff", "--name-only", "--cached"], cwd=root, text=True
            ).strip()
            untracked = subprocess.check_output(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=root,
                text=True,
            ).strip()

            if unstaged:
                files.update(unstaged.splitlines())
            if staged:
                files.update(staged.splitlines())
            if untracked:
                files.update(untracked.splitlines())

            changed = sorted(files)
        else:
            try:
                base = merge_base(compare_ref)
            except Exception:
                base = "HEAD~1"

            try:
                changed = changed_files_between(base, "HEAD")
            except Exception:
                tracked = subprocess.check_output(
                    ["git", "ls-files"], cwd=root, text=True
                ).strip()
                changed = tracked.splitlines() if tracked else []

        return recent_commit_head, changed

    finally:
        os.chdir(original_cwd)


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------

def _get_workflow_runner(workflow_type: str):
    """Dynamically import and return run_step from a step_workflows module."""
    try:
        module = __import__(
            f"betterci.step_workflows.{workflow_type}", fromlist=["run_step"]
        )
        return getattr(module, "run_step", None)
    except ImportError:
        return None


def _run_step(
    job: Job,
    step: Step,
    repo_root_path: Path,
    *,
    verbose: bool = False,
) -> None:
    """Execute a single step, routing to the appropriate handler."""
    console = get_console()

    # Route to step_workflow handler (lint, docker, etc.)
    if step.workflow_type:
        runner = _get_workflow_runner(step.workflow_type)
        if runner:
            runner(job, step, repo_root_path)
            return
        raise ValueError(
            f"Step '{step.name}' has workflow_type='{step.workflow_type}' "
            f"but betterci.step_workflows.{step.workflow_type} has no run_step()."
        )

    # Regular shell step
    cwd = (repo_root_path / (step.cwd or ".")).resolve()
    if not cwd.exists():
        raise FileNotFoundError(
            f"Step '{step.name}' working directory not found: {cwd}"
        )

    env = os.environ.copy()
    env.update(job.env or {})

    if verbose:
        # Stream output in real-time
        proc = subprocess.Popen(
            step.run,
            shell=True,
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
        proc.wait()
        returncode = proc.returncode
        stdout_text = ""
        stderr_text = ""
    else:
        proc_result = subprocess.run(
            step.run,
            shell=True,
            cwd=str(cwd),
            env=env,
            text=True,
            capture_output=True,
        )
        returncode = proc_result.returncode
        stdout_text = proc_result.stdout or ""
        stderr_text = proc_result.stderr or ""

        if stdout_text:
            print(stdout_text, end="")
        if stderr_text:
            print(stderr_text, end="", file=sys.stderr)

    if returncode != 0:
        hint = None
        cmd_lower = step.run.lower()
        for tool, tool_hint in TOOL_HINTS.items():
            if tool in cmd_lower:
                hint = tool_hint
                break
        if not hint:
            hint = f"Run the command locally to reproduce: {step.run!r}"

        raise StepFailure(
            job=job.name,
            step=step.name,
            cmd=step.run,
            exit_code=returncode,
        )


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

def _run_job(
    job: Job,
    repo_root_path: Path,
    cache: CacheStore,
    *,
    verbose: bool = False,
) -> Tuple[str, str]:
    """
    Execute a single job with pre-flight checks, caching, and timing.

    Returns (job_name, status) where status is:
      - "skipped(cache)"  — cache hit and cache_skip_on_hit=True
      - "ok"              — all steps succeeded
    Raises StepFailure or CIError on failures.
    """
    console = get_console()
    start_time = time.monotonic()

    console.print_job_start(job.name)

    # ------------------------------------------------------------------
    # Pre-flight: tools + secrets — fail fast before wasting any time
    # ------------------------------------------------------------------
    _run_preflight(job)

    # ------------------------------------------------------------------
    # Cache restore
    # ------------------------------------------------------------------
    if job.cache_enabled and job.cache_dirs:
        hit: CacheHit = cache.restore(job, repo_root=repo_root_path)
        if hit.hit:
            console.print_cache_hit(job.name, hit.reason)
            if job.cache_skip_on_hit:
                elapsed = time.monotonic() - start_time
                console.print_job_skipped(job.name, "cache hit", elapsed=elapsed)
                return job.name, "skipped(cache)"
        else:
            console.print_cache_miss(job.name)

    # ------------------------------------------------------------------
    # Step expansion (typed steps -> shell steps)
    # ------------------------------------------------------------------
    steps = _expand_steps(job)

    # ------------------------------------------------------------------
    # Execute steps
    # ------------------------------------------------------------------
    for step in steps:
        step_start = time.monotonic()
        console.print_step(step.name)
        try:
            _run_step(job, step, repo_root_path, verbose=verbose)
            step_elapsed = time.monotonic() - step_start
            console.print_success(step.name, elapsed=step_elapsed)
        except StepFailure as e:
            step_elapsed = time.monotonic() - step_start
            hint = None
            cmd_lower = e.cmd.lower()
            for tool, tool_hint in TOOL_HINTS.items():
                if tool in cmd_lower:
                    hint = tool_hint
                    break
            if not hint:
                hint = f"Run the command locally to reproduce: {e.cmd!r}"

            console.print_failure(
                e.step,
                str(e),
                exit_code=e.exit_code,
                hint=hint,
                elapsed=step_elapsed,
            )
            raise
        except CIError as e:
            step_elapsed = time.monotonic() - step_start
            console.print_failure(
                step.name,
                str(e),
                hint=e.details.get("hint"),
                elapsed=step_elapsed,
            )
            raise

    # ------------------------------------------------------------------
    # Cache save
    # ------------------------------------------------------------------
    if job.cache_enabled and job.cache_dirs:
        key, _manifest = cache.save(job, repo_root=repo_root_path)
        cache.prune(job.name, keep=job.cache_keep)
        console.print_cache_saved(job.name, key)

    elapsed = time.monotonic() - start_time
    console.print_job_done(job.name, elapsed=elapsed)
    return job.name, "ok"


# ---------------------------------------------------------------------------
# DAG construction
# ---------------------------------------------------------------------------

def _deps_of(job: Job) -> List[str]:
    return list(job.needs or [])


def _build_graph(
    jobs: List[Job],
) -> Tuple[Dict[str, Job], Dict[str, Set[str]], Dict[str, int]]:
    by_name: Dict[str, Job] = {}
    for j in jobs:
        if j.name in by_name:
            raise ValueError(f"Duplicate job name: {j.name!r}")
        by_name[j.name] = j

    adj: Dict[str, Set[str]] = {name: set() for name in by_name}
    indeg: Dict[str, int] = {name: 0 for name in by_name}

    for j in jobs:
        for d in _deps_of(j):
            if d not in by_name:
                raise ValueError(
                    f"Job '{j.name}' depends on '{d}' which does not exist. "
                    f"Available jobs: {sorted(by_name)}"
                )
            adj[d].add(j.name)
            indeg[j.name] += 1

    return by_name, adj, indeg


# ---------------------------------------------------------------------------
# Job selection (git-diff aware)
# ---------------------------------------------------------------------------

def _matches_any(path: str, patterns: List[str]) -> bool:
    return any(fnmatch(path, p) for p in patterns)


def select_jobs(
    jobs: List[Job],
    *,
    use_git_diff: bool,
    compare_ref: str,
    print_plan: bool,
) -> List[Job]:
    console = get_console()

    if not use_git_diff:
        if print_plan:
            console.print_plan_header()
            for j in jobs:
                console.print_plan_job(j.name, "git-diff disabled — always runs")
        return list(jobs)

    _head, changed = git_functionality(compare_ref=compare_ref)
    changed_set = set(changed or [])

    if print_plan:
        console.print_plan_header(compare_ref=compare_ref, changed_count=len(changed_set))

    selected: List[Job] = []
    for j in jobs:
        if not j.diff_enabled:
            selected.append(j)
            if print_plan:
                console.print_plan_job(j.name, "diff disabled — always runs")
            continue

        patterns = j.paths

        if not patterns:
            selected.append(j)
            if print_plan:
                console.print_plan_job(j.name, "no paths declared — always runs")
            continue

        matched = [f for f in changed_set if _matches_any(f, patterns)]
        if matched:
            selected.append(j)
            if print_plan:
                # Show at most 3 matched files to keep output concise
                sample = matched[:3]
                suffix = f" (+{len(matched) - 3} more)" if len(matched) > 3 else ""
                console.print_plan_job(
                    j.name,
                    f"matched {len(matched)} file(s): {', '.join(sample)}{suffix}",
                )
        else:
            if print_plan:
                console.print_plan_job_skipped(j.name, f"no files matched {patterns}")

    return selected


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_dag(
    jobs: List[Job],
    *,
    repo_root: str | Path = ".",
    cache_root: str | Path = ".betterci/cache",
    max_workers: int | None = None,
    fail_fast: bool = True,
    use_git_diff: bool = False,
    compare_ref: str = "origin/main",
    print_plan: bool = True,
    verbose: bool = False,
    safe: bool = False,
) -> Dict[str, str]:
    """
    Run a list of jobs respecting dependency order and in parallel where possible.

    Returns a dict of {job_name: status} where status is "ok", "failed",
    or "skipped(cache)".
    """
    repo_root_p = Path(repo_root).resolve()
    cache = CacheStore(cache_root)
    console = get_console()

    jobs = select_jobs(
        jobs,
        use_git_diff=use_git_diff,
        compare_ref=compare_ref,
        print_plan=print_plan,
    )

    if not jobs:
        console.print_info("No jobs selected.")
        return {}

    by_name, adj, indeg = _build_graph(jobs)
    ready: List[str] = [name for name, deg in indeg.items() if deg == 0]
    results: Dict[str, str] = {}
    failed = False

    if max_workers is None:
        c = os.cpu_count() or 2
        max_workers = max(1, c - 1)

    in_flight: Dict = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        while ready or in_flight:
            while ready and not (fail_fast and failed):
                name = ready.pop()
                fut = pool.submit(
                    _run_job,
                    by_name[name],
                    repo_root_p,
                    cache,
                    verbose=verbose,
                )
                in_flight[fut] = name

            if not in_flight:
                break

            fut = next(as_completed(list(in_flight.keys())))
            name = in_flight.pop(fut)

            try:
                job_name, status = fut.result()
                results[job_name] = status
            except (StepFailure, CIError):
                results[name] = "failed"
                failed = True
            except Exception as e:
                results[name] = "failed"
                console.print_error(
                    f"Unexpected error in job '{name}'",
                    str(e),
                    suggestion="Run with --debug for the full traceback.",
                )
                if console.debug:
                    console.print_exception(e)
                failed = True

            if results[name] in ("ok", "skipped(cache)"):
                for nxt in adj[name]:
                    indeg[nxt] -= 1
                    if indeg[nxt] == 0:
                        ready.append(nxt)
            else:
                failed = True

    return results


if __name__ == "__main__":
    jobs = load_workflow("betterci_workflow")
    res = run_dag(jobs)
    if any(v == "failed" for v in res.values()):
        raise SystemExit(1)
    raise SystemExit(0)
