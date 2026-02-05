from __future__ import annotations
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from dag import run_dag_pipeline
from model import Job  # using test_model, not model

# --- Errors (structured, explainable) ---

def git_functionality(
    compare_ref: str = "origin/main",
) -> Tuple[Optional[str], List[str]]:
    """
    Returns:
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
            # If dirty, include:
            # - staged changes
            # - unstaged changes
            # - untracked files
            files = set()

            # Unstaged + staged (compared to HEAD)
            # --name-only gives only paths, -z safer, but we'll keep it simple here.
            import subprocess

            unstaged = subprocess.check_output(
                ["git", "diff", "--name-only"],
                cwd=root,
                text=True,
            ).strip()

            staged = subprocess.check_output(
                ["git", "diff", "--name-only", "--cached"],
                cwd=root,
                text=True,
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
            # If clean, compare HEAD against merge-base with compare_ref
            # Fall back to HEAD~1 if origin/main isn't available.
            try:
                base = merge_base(compare_ref)
            except Exception:
                # e.g. no remote configured, first commit, etc.
                base = "HEAD~1"

            try:
                changed = changed_files_between(base, "HEAD")
            except Exception:
                # If HEAD~1 doesn't exist (first commit), treat all tracked files as "changed"
                import subprocess
                tracked = subprocess.check_output(
                    ["git", "ls-files"],
                    cwd=root,
                    text=True,
                ).strip()
                changed = tracked.splitlines() if tracked else []

        return recent_commit_head, changed

    finally:
        os.chdir(original_cwd)

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

# --- Planning (selection) ---

@dataclass(frozen=True)
class RunPlan:
    """
    Output of planning: what will run, what will be skipped, and why.
    This is intentionally small and serializable for future cloud runs.
    """
    selected_jobs: List[Job]
    skipped_jobs: List[Job]
    reasons: Dict[str, str]  # job_name -> reason string


def plan_run(
    jobs: List[Job],
    *,
    only: Optional[List[str]] = None,
) -> RunPlan:
    """
    Decide which jobs should run.

    For now (no git yet):
      - if `only` is None/empty => run all jobs
      - if `only` provided => run only jobs whose names match
        (and skip the rest)

    Later you’ll add git-based selection here (changed files → jobs).
    """
    by_name: Dict[str, Job] = {j.name: j for j in jobs}
    reasons: Dict[str, str] = {}

    if not only:
        for j in jobs:
            reasons[j.name] = "selected: default (run all)"
        return RunPlan(selected_jobs=list(jobs), skipped_jobs=[], reasons=reasons)

    # Normalize names and keep ordering stable (preserve original job order)
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

    # Optional: fail fast if user requested a job that doesn't exist
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


# --- Runner logic (executor) ---

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
        cwd = s.cwd

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


def run_pipeline(
    jobs: List[Job],
    max_workers: int | None = None,
    *,
    only: Optional[List[str]] = None,
) -> None:
    """
    Public entry point for executing a CI pipeline.

    Now includes planning:
      - plan which jobs should run
      - execute only those jobs in the DAG scheduler
    """
    plan = plan_run(jobs, only=only)

    # Optional: print plan summary (nice UX for hackathon demos)
    # for j in plan.selected_jobs:
    #     print(f"✓ {j.name} ({plan.reasons[j.name]})")
    # for j in plan.skipped_jobs:
    #     print(f"⏭ {j.name} ({plan.reasons[j.name]})")

    run_dag_pipeline(plan.selected_jobs, run_fn=run_job, max_workers=max_workers)
