# step_workflows/docker.py
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

from ..model import Job, Step


def run_step(job: Job, step: Step, repo_root: Path) -> None:
    """
    Execute a step inside a Docker container.
    Step metadata is read from step.meta (set by dsl.docker_step()).
    """
    from ..runner import StepFailure, TOOL_HINTS, CIError

    # Pre-flight: check Docker is available
    try:
        subprocess.run(["docker", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        hint = TOOL_HINTS.get("docker", "Install Docker and ensure the daemon is running.")
        raise CIError(
            kind="tool_unavailable",
            job=job.name,
            step=step.name,
            message="Docker is not available",
            details={"hint": hint},
        )

    image = step.meta.get("image")
    if not image:
        raise ValueError(
            f"[{job.name}] step '{step.name}' is missing 'image' in meta. "
            "Use dsl.docker_step() to create Docker steps."
        )

    repo_root_abs = repo_root.resolve()
    container_workdir = "/workspace"

    cmd = ["docker", "run", "--rm"]

    # Mount repo root
    cmd.extend(["-v", f"{repo_root_abs}:{container_workdir}"])

    # Additional volumes
    for vol in step.meta.get("volumes", []):
        cmd.extend(["-v", vol])

    # Working directory inside container
    step_cwd = step.cwd or "."
    container_cwd = f"{container_workdir}/{step_cwd}".replace("//", "/")
    cmd.extend(["-w", container_cwd])

    # Environment: os.environ + job.env + step.meta["env"]
    env = os.environ.copy()
    env.update(job.env or {})
    env.update(step.meta.get("env", {}))

    for key, value in env.items():
        cmd.extend(["-e", f"{key}={value}"])

    # Optional user
    user = step.meta.get("user", "")
    if user:
        cmd.extend(["--user", user])

    cmd.append(image)
    cmd.extend(["sh", "-c", step.run])

    proc = subprocess.run(
        cmd,
        shell=False,
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
            cmd=step.run,
            exit_code=proc.returncode,
        )
