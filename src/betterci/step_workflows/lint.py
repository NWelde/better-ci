# step_workflows/lint.py
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import List

from ..model import Job, Step


# ---------------------------------------------------------------------
# Lint step helper
# ---------------------------------------------------------------------

def lint_step(
    name: str,
    tool: str,
    args: str | None = None,
    *,
    cwd: str | None = None,
    files: List[str] | None = None,
) -> Step:
    """Create a lint step that runs a linting tool."""
    # Build the command - tool will be executed by run_step
    # For now, store the tool name and args separately
    cmd = tool
    if args:
        cmd = f"{tool} {args}"
    
    step = Step(name=name, run=cmd, cwd=cwd)
    # Attach lint metadata as dynamic attributes (like cache fields on Job)
    # Use object.__setattr__ to bypass frozen dataclass restriction
    object.__setattr__(step, "workflow_type", "lint")
    object.__setattr__(step, "lint_tool", tool)
    if args:
        object.__setattr__(step, "lint_args", args)
    if files:
        object.__setattr__(step, "lint_files", files)
    return step


# ---------------------------------------------------------------------
# Lint step execution
# ---------------------------------------------------------------------

def _check_tool_available(tool: str) -> None:
    """Check if a linting tool is available, raise helpful error if not."""
    # Import here to avoid circular import
    from ..runner import TOOL_HINTS, CIError

    try:
        subprocess.run(
            [tool, "--version"],
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        hint = TOOL_HINTS.get(tool, f"Install {tool} or fix PATH.")
        raise CIError(
            kind="tool_unavailable",
            job="",
            step=None,
            message=f"{tool} is not available",
            details={"hint": hint, "tool": tool},
        )


def run_step(job: Job, step: Step, repo_root: Path) -> None:
    """Run a lint step."""
    # Import here to avoid circular import
    from ..runner import StepFailure

    tool = getattr(step, "lint_tool", None)
    if not tool:
        raise ValueError(f"[{job.name}] step '{step.name}' has no lint_tool attribute")

    _check_tool_available(tool)

    # Build the command
    cmd_parts = [tool]
    
    # Add args if specified
    lint_args = getattr(step, "lint_args", None)
    if lint_args:
        # Split args string into list, handling quoted strings
        cmd_parts.extend(shlex.split(lint_args))
    
    # Add files if specified, otherwise default to current directory
    lint_files = getattr(step, "lint_files", None)
    if lint_files:
        cmd_parts.extend(lint_files)
    else:
        # Default to current directory (or step.cwd if specified)
        target_dir = step.cwd or "."
        cmd_parts.append(target_dir)

    # Determine working directory
    cwd = (repo_root / (step.cwd or ".")).resolve()
    if not cwd.exists():
        raise FileNotFoundError(f"[{job.name}] step '{step.name}' cwd not found: {cwd}")

    # Merge environment variables
    env = os.environ.copy()
    env.update(getattr(job, "env", {}) or {})

    # Execute linting command
    proc = subprocess.run(
        cmd_parts,
        shell=False,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
    )
    
    if proc.returncode != 0:
        raise StepFailure(job=job.name, step=step.name, cmd=" ".join(cmd_parts), exit_code=proc.returncode)
