# step_workflows/docker.py
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, List

from ..model import Job, Step


# ---------------------------------------------------------------------
# Docker step helper
# ---------------------------------------------------------------------

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
    """Create a shell step that runs in a Docker container."""
    step = Step(name=name, run=cmd, cwd=cwd)
    # Attach Docker metadata as dynamic attributes (like cache fields on Job)
    # Use object.__setattr__ to bypass frozen dataclass restriction
    object.__setattr__(step, "workflow_type", "docker")
    object.__setattr__(step, "docker_image", image)
    if volumes:
        object.__setattr__(step, "docker_volumes", volumes)
    if env:
        object.__setattr__(step, "docker_env", env)
    if user:
        object.__setattr__(step, "docker_user", user)
    return step


# ---------------------------------------------------------------------
# Docker step execution
# ---------------------------------------------------------------------

def _check_docker_available() -> None:
    """Check if Docker is available, raise helpful error if not."""
    # Import here to avoid circular import
    from ..runner import TOOL_HINTS, CIError

    try:
        subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        hint = TOOL_HINTS.get("docker", "Install Docker and ensure the daemon is running.")
        raise CIError(
            kind="docker_unavailable",
            job="",
            step=None,
            message="Docker is not available",
            details={"hint": hint},
        )


def run_step(job: Job, step: Step, repo_root: Path) -> None:
    """Run a step inside a Docker container."""
    # Import here to avoid circular import
    from ..runner import StepFailure

    _check_docker_available()

    image = getattr(step, "docker_image", None)
    repo_root_abs = repo_root.resolve()
    container_workdir = "/workspace"

    # Build docker run command
    cmd = ["docker", "run", "--rm"]

    # Volume mount: repo_root -> /workspace
    cmd.extend(["-v", f"{repo_root_abs}:{container_workdir}"])

    # Additional volumes if specified
    extra_volumes = getattr(step, "docker_volumes", None)
    if extra_volumes:
        for vol in extra_volumes:
            cmd.extend(["-v", vol])

    # Working directory: /workspace/<relative_cwd>
    step_cwd = step.cwd or "."
    container_cwd = f"{container_workdir}/{step_cwd}".replace("//", "/")
    cmd.extend(["-w", container_cwd])

    # Environment variables: merge os.environ + job.env + step.docker_env
    env = os.environ.copy()
    env.update(getattr(job, "env", {}) or {})
    step_env = getattr(step, "docker_env", None)
    if step_env:
        env.update(step_env)

    for key, value in env.items():
        cmd.extend(["-e", f"{key}={value}"])

    # User if specified
    docker_user = getattr(step, "docker_user", None)
    if docker_user:
        cmd.extend(["--user", docker_user])

    # Image and command
    cmd.append(image)
    cmd.extend(["sh", "-c", step.run])

    # stream output to terminal (good DX for hackathon)
    proc = subprocess.run(
        cmd,
        shell=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise StepFailure(job=job.name, step=step.name, cmd=step.run, exit_code=proc.returncode)
