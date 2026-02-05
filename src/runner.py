# runner.py
#TODO: Make failure outputs clearer that is the only issue thus far other funcitonality works 
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from dag import run_dag_pipeline
from model_test import Job, Step  # <-- using test_model instead of model


# TODO: add git implementation so better-ci can follow the path:
# local dev ---> commit ---> CI ---> push ---> cloud CI


# --- Errors (structured, explainable) ---

@dataclass
class CIError(Exception):
    """
    A structured error type so failures are:
      - consistent (same fields across all failures)
      - explainable (includes context + last log lines)
      - easy to render in UI later (kind/job/step/details)
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


# --- Runner logic (executor) ---
# This is what dag.py calls for each job in the DAG.

def run_job(job: Job) -> None:
    """
    Execute a single job:
      1) preflight checks (tools, directories)
      2) execute job steps
      3) raise CIError with structured details on failure
    """
    # 1) PRE-FLIGHT: tools required by this job
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

    # PRE-FLIGHT: validate step working directories (if provided)
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
                capture_output=True,  # later you can stream output live
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
    Run the full pipeline respecting DAG dependencies.

    - dag.py is the scheduler/orchestrator (it computes stages + parallelizes)
    - runner.py supplies run_job (the executor with structured CIError)
    """
    try:
        run_dag_pipeline(jobs, run_fn=run_job, max_workers=max_workers)
    except CIError as e:
        print("\n=== CI ERROR ===")
        print(str(e))
        raise


# -------------------------
# Test scenario (so you can run `python3 runner.py` immediately)
# -------------------------

def make_test_jobs() -> list[Job]:
    """
    A small DAG to test scheduling + parallelism + failure handling:

        setup
         /  \
      lint  unit
        \    /
        package
          |
         e2e (fails)
    """
    return [
        Job(
            name="setup",
            needs=[],
            requires=["python3"],
            steps=[
                Step(name="hello", run='python3 -c "print(\\"setup done\\")"'),
            ],
        ),
        Job(
            name="lint",
            needs=["setup"],
            steps=[
                Step(
                    name="sleep",
                    run='python3 -c "import time; time.sleep(1); print(\\"lint ok\\")"',
                ),
            ],
        ),
        Job(
            name="unit",
            needs=["setup"],
            steps=[
                Step(
                    name="sleep",
                    run='python3 -c "import time; time.sleep(1); print(\\"unit ok\\")"',
                ),
            ],
        ),
        Job(
            name="package",
            needs=["lint", "unit"],
            steps=[
                Step(name="pkg", run='python3 -c "print(\\"package ok\\")"'),
            ],
        ),
        Job(
            name="e2e",
            needs=["package"],
            steps=[
                # Intentionally failing step to test your CIError formatting:
                Step(
                    name="run",
                    run='python3 -c "import sys; print(\\"e2e failing\\"); sys.exit(2)"',
                ),
            ],
        ),
    ]


def main():
    # For now, run the built-in test scenario.
    # Later, replace this with your config loader (YAML/Python DSL/etc).
    jobs = make_test_jobs()
    run_pipeline(jobs, max_workers=4)


if __name__ == "__main__":
    main()
