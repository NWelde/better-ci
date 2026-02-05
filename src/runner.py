import os
import shutil
import subprocess
from dataclasses import dataclass

#TODO: I will have to add git implementation so better-ci is abel to follow the path LD ---> commit ---> CI ---> push ----> cloud CI
# --- Errors (structured, explainable) ---

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
}


# --- Runner logic ---

def run_job(job) -> None:
    # 1) PRE-FLIGHT (merged into job execution)
    missing = []
    for tool in getattr(job, "requires", []):
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

    # (optional preflight) validate step cwd exists
    for s in job.steps:
        if s.cwd is not None and not os.path.isdir(s.cwd):
            raise CIError(
                kind="BadWorkingDirectory",
                job=job.name,
                step=s.name,
                message="Step cwd does not exist",
                details={"cwd": s.cwd},
            )

    # 2) EXECUTE STEPS
    env = os.environ.copy()
    env.update(getattr(job, "env", {}) or {})

    for s in job.steps:
        try:
            completed = subprocess.run(
                s.run,
                shell=True,
                cwd=s.cwd,
                env=env,
                text=True,
                capture_output=True,   # swap to streaming later
            )
        except OSError as e:
            raise CIError(
                kind="SpawnFailed",
                job=job.name,
                step=s.name,
                message=str(e),
                details={"command": s.run, "cwd": s.cwd or "."},
            )

        if completed.returncode != 0:
            # show last lines for explainability
            tail = (completed.stdout + "\n" + completed.stderr).strip().splitlines()[-30:]
            raise CIError(
                kind="StepFailed",
                job=job.name,
                step=s.name,
                message=f"Command exited with code {completed.returncode}",
                details={
                    "command": s.run,
                    "cwd": s.cwd or ".",
                    "exit_code": completed.returncode,
                    "log_tail": "\n".join(tail),
                },
            )
