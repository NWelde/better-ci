# step_workflows/lint.py
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List

from ..model import Job, Step


def run_step(job: Job, step: Step, repo_root: Path) -> None:
    """
    Execute a lint step.
    Step metadata is read from step.meta (set by dsl.lint_step()).
    """
    from ..runner import StepFailure, TOOL_HINTS, CIError

    tool = step.meta.get("tool")
    if not tool:
        raise ValueError(
            f"[{job.name}] step '{step.name}' is missing 'tool' in meta. "
            "Use dsl.lint_step() to create lint steps."
        )

    # Pre-flight: check tool is available
    try:
        subprocess.run([tool, "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        hint = TOOL_HINTS.get(tool, f"Install {tool!r} or fix your PATH.")
        raise CIError(
            kind="tool_unavailable",
            job=job.name,
            step=step.name,
            message=f"Lint tool not found: {tool!r}",
            details={"tool": tool, "hint": hint},
        )

    # Build command
    cmd_parts = [tool]
    args_str = step.meta.get("args", "")
    if args_str:
        cmd_parts.extend(shlex.split(args_str))

    files: List[str] = step.meta.get("files", [])
    if files:
        cmd_parts.extend(files)
    else:
        cmd_parts.append(step.cwd or ".")

    cwd = (repo_root / (step.cwd or ".")).resolve()
    if not cwd.exists():
        raise FileNotFoundError(
            f"[{job.name}] step '{step.name}' working directory not found: {cwd}"
        )

    env = os.environ.copy()
    env.update(job.env or {})

    proc = subprocess.run(
        cmd_parts,
        shell=False,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
    )

    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)

    if proc.returncode != 0:
        raise StepFailure(
            job=job.name,
            step=step.name,
            cmd=" ".join(cmd_parts),
            exit_code=proc.returncode,
        )
