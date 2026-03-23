# agent/executor.py
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from betterci.cache import CacheStore
from betterci.model import Job, Step
from betterci.runner import _run_job

from .api_client import APIClient
from .models import ExecutionResult, Lease


# ---------------------------------------------------------------------------
# Log capture
# ---------------------------------------------------------------------------

class LogCapture:
    """
    Context manager that captures stdout/stderr into a buffer for later
    submission to the cloud API via complete_lease().
    """

    def __init__(self):
        self.log_buffer = io.StringIO()
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr

    def __enter__(self) -> "LogCapture":
        sys.stdout = self  # type: ignore[assignment]
        sys.stderr = self  # type: ignore[assignment]
        return self

    def __exit__(self, *_) -> None:
        sys.stdout = self._orig_stdout
        sys.stderr = self._orig_stderr

    def write(self, text: str) -> int:
        self.log_buffer.write(text)
        # Also forward to original stderr so the agent itself stays visible
        self._orig_stderr.write(text)
        return len(text)

    def flush(self) -> None:
        self._orig_stderr.flush()

    def get_logs(self) -> str:
        return self.log_buffer.getvalue()


# ---------------------------------------------------------------------------
# Repository checkout
# ---------------------------------------------------------------------------

def _clone_or_update_repo(repo_url: str, ref: str, work_dir: Path) -> Path:
    """
    Clone or update a repository at the given ref.
    Returns the path to the checked-out repository.

    Security: repo_url is passed directly to git. Callers should ensure it
    comes from a trusted source (the cloud API, which only accepts authenticated
    submissions when API_KEY is configured).
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    # Use the last URL segment as the local directory name
    repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    # Basic sanitization: strip path separators to prevent directory traversal
    repo_name = repo_name.replace("/", "_").replace("\\", "_") or "repo"
    repo_path = work_dir / repo_name

    try:
        if repo_path.exists():
            result = subprocess.run(
                ["git", "fetch", "origin"],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git fetch failed: {result.stderr.strip()}")
        else:
            result = subprocess.run(
                ["git", "clone", repo_url, str(repo_path)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git clone failed: {result.stderr.strip()}")

        result = subprocess.run(
            ["git", "checkout", ref],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git checkout {ref!r} failed: {result.stderr.strip()}"
            )

    except FileNotFoundError:
        raise RuntimeError("git not found. Install Git on the agent machine.")
    except subprocess.SubprocessError as e:
        raise RuntimeError(f"Git operation failed: {e}")

    return repo_path


# ---------------------------------------------------------------------------
# Job serialization (Job <-> dict)
# ---------------------------------------------------------------------------

def job_to_dict(job: Job) -> Dict[str, Any]:
    """Serialize a Job model to a plain dict for API submission."""
    steps = []
    for step in job.steps:
        d: Dict[str, Any] = {"name": step.name, "run": step.run}
        if step.cwd is not None:
            d["cwd"] = step.cwd
        if step.kind is not None:
            d["kind"] = step.kind
        if step.data is not None:
            d["data"] = step.data
        if step.workflow_type is not None:
            d["workflow_type"] = step.workflow_type
        if step.meta:
            d["meta"] = step.meta
        steps.append(d)

    return {
        "name":              job.name,
        "steps":             steps,
        "needs":             job.needs,
        "inputs":            job.inputs,
        "env":               job.env,
        "requires":          job.requires,
        "secrets":           job.secrets,
        "paths":             job.paths,
        "diff_enabled":      job.diff_enabled,
        "cache_dirs":        job.cache_dirs,
        "cache_enabled":     job.cache_enabled,
        "cache_skip_on_hit": job.cache_skip_on_hit,
        "cache_keep":        job.cache_keep,
    }


def _dict_to_job(d: Dict[str, Any]) -> Job:
    """Deserialize a plain dict back to a Job model."""
    steps = []
    for sd in d.get("steps", []):
        steps.append(Step(
            name=sd["name"],
            run=sd.get("run", ""),
            cwd=sd.get("cwd"),
            kind=sd.get("kind"),
            data=sd.get("data"),
            workflow_type=sd.get("workflow_type"),
            meta=sd.get("meta") or {},
        ))

    return Job(
        name=d["name"],
        steps=steps,
        needs=d.get("needs", []),
        inputs=d.get("inputs", []),
        env=d.get("env", {}),
        requires=d.get("requires", []),
        secrets=d.get("secrets", []),
        paths=d.get("paths"),
        diff_enabled=d.get("diff_enabled", True),
        cache_dirs=d.get("cache_dirs", []),
        cache_enabled=d.get("cache_enabled", True),
        cache_skip_on_hit=d.get("cache_skip_on_hit", False),
        cache_keep=d.get("cache_keep", 3),
    )


# ---------------------------------------------------------------------------
# Lease execution
# ---------------------------------------------------------------------------

def execute_lease(
    lease: Lease,
    api_client: APIClient,
    work_dir: Path,
    cache_root: Path = Path(".betterci/cache"),
) -> ExecutionResult:
    """
    Execute a job lease end-to-end:
      1. Clone/update the repository.
      2. Deserialize the job from the lease payload.
      3. Run the job using the standard runner (with pre-flight checks, caching, etc.).
      4. Return the result.

    All stdout/stderr is captured for submission to the cloud API.
    """
    log_capture = LogCapture()

    try:
        with log_capture:
            repo_path = _clone_or_update_repo(
                lease.repo_url, lease.ref, work_dir
            )
            job = _dict_to_job(lease.job)
            cache = CacheStore(cache_root)

            try:
                job_name, status = _run_job(job, repo_path, cache)
                job_results: Dict[str, Any] = {
                    "job_name": job_name,
                    "status": status,
                }
                execution_status = "ok" if status != "failed" else "failed"
            except Exception as e:
                print(f"Job execution error: {e}", file=sys.stderr)
                raise

        logs = log_capture.get_logs()
        return ExecutionResult(
            status=execution_status,
            logs=logs,
            job_results=job_results,
        )

    except Exception as e:
        logs = log_capture.get_logs()
        error_msg = str(e)
        if error_msg and error_msg not in logs:
            logs = f"{logs}\nError: {error_msg}".strip()

        return ExecutionResult(
            status="failed",
            logs=logs,
            job_results={"error": error_msg, "error_type": type(e).__name__},
            error=error_msg,
        )
