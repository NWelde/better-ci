from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Dict, List, Optional, Set, Tuple

from dag import run_dag_pipeline
from model import Job  # your DSL Job

# Git helpers (from your git_facts module)
from git_facts.git import (
    head_sha,
    repo_root,
    is_dirty,
    merge_base,
    changed_files as git_changed_files,  # alias to avoid name collision
)

# ----------------------------------------------------------------------
# Git diff functionality
# ----------------------------------------------------------------------

def git_functionality(compare_ref: str = "origin/main") -> Tuple[Optional[str], List[str]]:
    """
    Returns:
      recent_commit_head:
        - full SHA for HEAD if repo is clean
        - None if repo has uncommitted changes (dirty)
      changed:
        - list of changed file paths relative to repo root
    """
    root_path = repo_root()
    original_cwd = os.getcwd()

    try:
        os.chdir(root_path)

        dirty = is_dirty()
        recent_commit_head: Optional[str] = None if dirty else head_sha()

        if dirty:
            # Dirty working tree: include staged, unstaged, and untracked changes.
            files: Set[str] = set()

            unstaged = subprocess.check_output(
                ["git", "diff", "--name-only"],
                cwd=root_path,
                text=True,
            ).strip()

            staged = subprocess.check_output(
                ["git", "diff", "--name-only", "--cached"],
                cwd=root_path,
                text=True,
            ).strip()

            untracked = subprocess.check_output(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=root_path,
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
            # Clean repo: compare from merge-base(compare_ref) to HEAD
            try:
                base = merge_base(compare_ref)
            except Exception:
                # e.g. no remote, unusual history, etc.
                base = "HEAD~1"

            try:
                changed = git_changed_files(base, "HEAD")
            except Exception:
                # If HEAD~1 doesn't exist (first commit), treat all tracked files as changed
                tracked = subprocess.check_output(
                    ["git", "ls-files"],
                    cwd=root_path,
                    text=True,
                ).strip()
                changed = tracked.splitlines() if tracked else []

        return recent_commit_head, changed

    finally:
        os.chdir(original_cwd)


# ----------------------------------------------------------------------
# Structured Errors
# ----------------------------------------------------------------------

@dataclass
class CIError(Exception):
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
            lines.append(f"{k}={v}")
        return "\n".join(lines)


TOOL_HINTS = {
    "npm": "Install Node.js (includes npm) or fix PATH.",
    "node": "Install Node.js or fix PATH.",
    "pytest": "Install pytest (e.g., pip install pytest).",
    "ruff": "Install ruff (e.g., pip install ruff).",
    "docker": "Install Docker and ensure the daemon is running.",
    "python3": "Install Python 3 or fix PATH (python3).",
}


# ----------------------------------------------------------------------
# Planning (selection)
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class RunPlan:
    """
    Output of planning: what will run, what will be skipped, and why.
    This is intentionally small and serializable for future cloud runs.
    """
    selected_jobs: List[Job]
    skipped_jobs: List[Job]
    reasons: Dict[str, str]  # job_name -> reason string


def _matches_any(path: str, patterns: List[str]) -> bool:
    """
    Simple glob matching via fnmatch.
    Patterns like "backend/*" work well.
    Note: "**" behavior may vary by platform—good enough for hackathon scope.
    """
    return any(fnmatch(path, p) for p in patterns)


def plan_run(
    jobs: List[Job],
    *,
    only: Optional[List[str]] = None,
    use_git_diff: bool = False,
    changed_files: Optional[List[str]] = None,
) -> RunPlan:
    """
    Decide which jobs should run.

    Priority:
      1) --only (explicit user request) wins
      2) if git diff disabled -> run all
      3) if git diff enabled:
           - if changed_files unavailable -> run all (safe fallback)
           - else filter by job.paths (glob patterns), unless job.diff_enabled is False
    """
    by_name: Dict[str, Job] = {j.name: j for j in jobs}
    reasons: Dict[str, str] = {}

    # 1) Explicit user selection
    if only:
        only_set: Set[str] = set(only)
        selected: List[Job] = []
        skipped: List[Job] = []

        for j in jobs:
            if j.name in only_set:
                selected.append(j)
                reasons[j.name] = "selected: user requested via --only"
            else:
                skipped.append(j)
                reasons[j.name] = "skipped: not in --only list"

        missing = [name for name in only_set if name not in by_name]
        if missing:
            raise CIError(
                kind="UnknownJob",
                job="<planner>",
                step=None,
                message=f"Unknown job(s) requested: {', '.join(missing)}",
                details={"known_jobs": sorted(by_name.keys())},
            )

        return RunPlan(selected_jobs=selected, skipped_jobs=skipped, reasons=reasons)

    # 2) Default behavior: run all when git diff disabled
    if not use_git_diff:
        for j in jobs:
            reasons[j.name] = "selected: default (git diff disabled)"
        return RunPlan(selected_jobs=list(jobs), skipped_jobs=[], reasons=reasons)

    # 3) Git diff enabled but unavailable -> run all (safe fallback)
    if changed_files is None:
        for j in jobs:
            reasons[j.name] = "selected: git diff enabled but changed_files unavailable (safe fallback)"
        return RunPlan(selected_jobs=list(jobs), skipped_jobs=[], reasons=reasons)

    changed_set = set(changed_files)
    selected: List[Job] = []
    skipped: List[Job] = []

    for j in jobs:
        # Per-job opt-out: always run even when diff is enabled
        if getattr(j, "diff_enabled", True) is False:
            selected.append(j)
            reasons[j.name] = "selected: diff disabled for this job"
            continue

        patterns = getattr(j, "paths", None)

        # Safe default: if no patterns declared, run it
        if not patterns:
            selected.append(j)
            reasons[j.name] = "selected: no paths specified (default run)"
            continue

        hit = any(_matches_any(f, patterns) for f in changed_set)
        if hit:
            selected.append(j)
            reasons[j.name] = f"selected: changes matched paths {patterns}"
        else:
            skipped.append(j)
            reasons[j.name] = f"skipped: no changes matched paths {patterns}"

    return RunPlan(selected_jobs=selected, skipped_jobs=skipped, reasons=reasons)


# ----------------------------------------------------------------------
# Runner logic (executor)
# ----------------------------------------------------------------------

def run_job(job: Job) -> None:
    """
    Execute a single job:
      1) preflight checks (tools, directories)
      2) execute job steps
      3) raise CIError with structured details on failure
    """
    missing = []
    for tool in getattr(job, "requires", []) or []:
        if shutil.which(tool) is None:
            missing.append(tool)

    if missing:
        hints = {t: TOOL_HINTS.get(t, "Install it and ensure it is on PATH.") for t in missing}
        raise CIError(
            kind="MissingTools",
            job=job.name,
            step=None,
            message=f"Required tools not found: {', '.join(missing)}",
            details={
                "PATH": os.environ.get("PATH", ""),
                "hints": hints,
            },
        )

    for s in getattr(job, "steps", []) or []:
        if getattr(s, "cwd", None) is not None and not os.path.isdir(s.cwd):
            raise CIError(
                kind="BadWorkingDirectory",
                job=job.name,
                step=getattr(s, "name", None) or "<unnamed-step>",
                message="Step cwd does not exist",
                details={"cwd": s.cwd},
            )

    env = os.environ.copy()
    env.update(getattr(job, "env", {}) or {})

    for s in job.steps:
        step_name = getattr(s, "name", None) or "<unnamed-step>"
        cmd = s.run
        cwd = getattr(s, "cwd", None)

        try:
            completed = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                env=env,
                text=True,
                capture_output=True,
            )
        except OSError as e:
            raise CIError(
                kind="SpawnFailed",
                job=job.name,
                step=step_name,
                message=str(e),
                details={"command": cmd, "cwd": cwd or "."},
            )

        if completed.returncode != 0:
            combined = (completed.stdout or "") + "\n" + (completed.stderr or "")
            tail_lines = combined.strip().splitlines()[-30:]
            raise CIError(
                kind="StepFailed",
                job=job.name,
                step=step_name,
                message=f"Command exited with code {completed.returncode}",
                details={
                    "command": cmd,
                    "cwd": cwd or ".",
                    "exit_code": completed.returncode,
                    "log_tail": "\n".join(tail_lines),
                },
            )


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def run_pipeline(
    jobs: List[Job],
    max_workers: int | None = None,
    *,
    only: Optional[List[str]] = None,
    use_git_diff: bool = False,          # users can disable/enable in DSL
    compare_ref: str = "origin/main",     # branch to diff against
) -> None:
    """
    Execute a CI pipeline with optional git-diff based job selection.

    - If `only` is provided: run exactly those jobs, ignoring git diff
    - If `use_git_diff` is False: run all jobs
    - If `use_git_diff` is True:
        - compute changed files (merge-base..HEAD if clean, staged/unstaged/untracked if dirty)
        - select jobs using job.paths (glob patterns)
        - jobs can opt-out via job.diff_enabled = False
    """
    changed: Optional[List[str]] = None

    # Only compute changed files if we plan to use them
    if use_git_diff and not only:
        _head, changed = git_functionality(compare_ref=compare_ref)

    plan = plan_run(
        jobs,
        only=only,
        use_git_diff=use_git_diff,
        changed_files=changed,
    )

    # Optional: print plan (nice for demos)
    # for j in plan.selected_jobs:
    #     print(f"✓ {j.name} ({plan.reasons[j.name]})")
    # for j in plan.skipped_jobs:
    #     print(f"⏭ {j.name} ({plan.reasons[j.name]})")

    run_dag_pipeline(plan.selected_jobs, run_fn=run_job, max_workers=max_workers)
