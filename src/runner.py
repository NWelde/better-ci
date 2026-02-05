# runner.py
# TODO: Make failure outputs clearer (only remaining UX issue)
from __future__ import annotations
import os
import shutil
import subprocess
from dataclasses import dataclass
from dag import run_dag_pipeline
from model import Job  # using test_model, not model
from git_facts.git import changed_files, merge_base, repo_root, head_sha, is_dirty

# TODO: add git implementation so better-ci can follow the path:
# local dev ---> commit ---> CI ---> push ---> cloud CI

# --- Errors (structured, explainable) ---

@dataclass
class CIError(Exception):
    """
    Structured CI error with enough context for:
      - clean CLI output
      - future UI rendering
      - debugging without full tracebacks
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

import os
from pathlib import Path
from typing import List, Optional, Tuple

from git_facts.git import repo_root, head_sha, is_dirty, merge_base, changed_files as changed_files_between


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


# --- Runner logic (executor) ---
# This function is called by dag.py for each job in the DAG.

def run_job(job: Job) -> None:
    """
    Execute a single job:
      1) preflight checks (tools, directories)
      2) execute job steps
      3) raise CIError with structured details on failure
    """
    # 1) PRE-FLIGHT: required tools
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

    # PRE-FLIGHT: validate step working directories
    for s in getattr(job, "steps", []) or []:
        if getattr(s, "cwd", None) is not None and not os.path.isdir(s.cwd):
            raise CIError(
                kind="BadWorkingDirectory",
                job=job.name,
                step=getattr(s, "name", None) or "<unnamed-step>",
                message="Step cwd does not exist",
                details={"cwd": s.cwd},
            )

    # 2) EXECUTE STEPS

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
                capture_output=True,  # can be streamed later
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


def run_pipeline(jobs: list[Job], max_workers: int | None = None) -> None:
    """
    Public entry point for executing a CI pipeline.

    Responsibilities:
      - delegate scheduling + parallelism to dag.py
      - provide run_job as the executor
      - surface CIError cleanly to caller
    """
    run_dag_pipeline(jobs, run_fn=run_job, max_workers=max_workers)


