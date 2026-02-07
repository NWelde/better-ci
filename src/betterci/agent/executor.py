# agent/executor.py
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

from betterci.cache import CacheStore
from betterci.model import Job, Step
from betterci.runner import _run_job

from .api_client import APIClient
from .models import ExecutionResult, Lease


class LogCapture:
    """
    Context manager that captures stdout/stderr for later submission to API.
    
    Logs are captured in a buffer and sent at job completion via complete_lease().
    This ensures all logs (including from subprocesses) are captured even if
    exceptions occur during execution.
    """
    
    def __init__(self, api_client: APIClient, job_id: str):
        self.api_client = api_client
        self.job_id = job_id
        self.log_buffer = io.StringIO()
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        
    def __enter__(self):
        # Redirect stdout and stderr to our buffer
        sys.stdout = self
        sys.stderr = self
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore original streams
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr
        
    def write(self, text: str) -> int:
        """Write to buffer."""
        self.log_buffer.write(text)
        return len(text)
        
    def flush(self) -> None:
        """Flush buffer (no-op, logs are sent at completion)."""
        pass
    
    def get_logs(self) -> str:
        """Get all captured logs."""
        return self.log_buffer.getvalue()


def _clone_or_update_repo(repo_url: str, ref: str, work_dir: Path) -> Path:
    """
    Clone or update a repository at the specified ref.
    
    Args:
        repo_url: Git repository URL
        ref: Git reference (branch, tag, or commit SHA)
        work_dir: Base directory for checkouts
        
    Returns:
        Path to the checked out repository
        
    Raises:
        RuntimeError: If git operations fail
    """
    # Create work directory if it doesn't exist
    work_dir.mkdir(parents=True, exist_ok=True)
    
    # Use a sanitized version of repo_url as directory name
    # Simple approach: use last part of URL
    repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    repo_path = work_dir / repo_name
    
    try:
        if repo_path.exists():
            # Update existing repo
            result = subprocess.run(
                ["git", "fetch", "origin"],
                cwd=repo_path,
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git fetch failed: {result.stderr}")
            
            result = subprocess.run(
                ["git", "checkout", ref],
                cwd=repo_path,
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git checkout {ref} failed: {result.stderr}")
        else:
            # Clone new repo
            result = subprocess.run(
                ["git", "clone", repo_url, str(repo_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git clone failed: {result.stderr}")
            
            result = subprocess.run(
                ["git", "checkout", ref],
                cwd=repo_path,
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git checkout {ref} failed: {result.stderr}")
    except subprocess.SubprocessError as e:
        raise RuntimeError(f"Git operation failed: {e}")
    except FileNotFoundError:
        raise RuntimeError("git command not found. Please install Git.")
    
    return repo_path


def job_to_dict(job: Job) -> dict:
    """
    Convert a Job model to a dictionary for API submission.
    This is the reverse of _dict_to_job().
    
    Args:
        job: Job model instance
        
    Returns:
        Dictionary with job definition
    """
    # Convert steps to list of dicts
    steps = []
    for step in job.steps:
        step_dict = {
            "name": step.name,
            "run": step.run,
        }
        if step.cwd is not None:
            step_dict["cwd"] = step.cwd
        if step.kind is not None:
            step_dict["kind"] = step.kind
        if step.data is not None:
            step_dict["data"] = step.data
        steps.append(step_dict)
    
    # Build job dict with required fields
    job_dict = {
        "name": job.name,
        "steps": steps,
        "needs": job.needs,
        "inputs": job.inputs,
        "env": job.env,
        "requires": job.requires,
        "diff_enabled": job.diff_enabled,
    }
    
    # Add optional fields if present
    if job.paths is not None:
        job_dict["paths"] = job.paths
    
    # Add cache fields if present (using getattr for optional fields)
    if hasattr(job, "cache_dirs"):
        job_dict["cache_dirs"] = getattr(job, "cache_dirs")
    if hasattr(job, "cache_enabled"):
        job_dict["cache_enabled"] = getattr(job, "cache_enabled")
    if hasattr(job, "cache_skip_on_hit"):
        job_dict["cache_skip_on_hit"] = getattr(job, "cache_skip_on_hit")
    if hasattr(job, "cache_keep"):
        job_dict["cache_keep"] = getattr(job, "cache_keep")
    
    return job_dict


def _dict_to_job(job_dict: dict) -> Job:
    """
    Convert a job dictionary from API to a Job model.
    
    Args:
        job_dict: Dictionary with job definition
        
    Returns:
        Job model instance
    """
    # Convert steps
    steps = []
    for step_dict in job_dict.get("steps", []):
        step = Step(
            name=step_dict["name"],
            run=step_dict.get("run", ""),
            cwd=step_dict.get("cwd"),
            kind=step_dict.get("kind"),
            data=step_dict.get("data"),
        )
        steps.append(step)
    
    # Create Job
    job = Job(
        name=job_dict["name"],
        steps=steps,
        needs=job_dict.get("needs", []),
        inputs=job_dict.get("inputs", []),
        env=job_dict.get("env", {}),
        requires=job_dict.get("requires", []),
        paths=job_dict.get("paths"),
        diff_enabled=job_dict.get("diff_enabled", True),
    )
    
    # Set cache fields if present (using setattr for optional fields)
    if "cache_dirs" in job_dict:
        setattr(job, "cache_dirs", job_dict["cache_dirs"])
    if "cache_enabled" in job_dict:
        setattr(job, "cache_enabled", job_dict["cache_enabled"])
    if "cache_skip_on_hit" in job_dict:
        setattr(job, "cache_skip_on_hit", job_dict["cache_skip_on_hit"])
    if "cache_keep" in job_dict:
        setattr(job, "cache_keep", job_dict["cache_keep"])
    
    return job


def execute_lease(
    lease: Lease,
    api_client: APIClient,
    work_dir: Path,
    cache_root: Path = Path(".betterci/cache"),
) -> ExecutionResult:
    """
    Execute a job lease.
    
    Args:
        lease: Lease object with job details
        api_client: API client for submitting results
        work_dir: Directory for repository checkouts
        cache_root: Root directory for cache storage
        
    Returns:
        ExecutionResult with status and logs
    """
    logs = ""
    job_results = {}
    log_capture = LogCapture(api_client, lease.job_id)
    
    try:
        # Start log capture early to capture all output including setup errors
        with log_capture:
            # Clone/checkout repository
            repo_path = _clone_or_update_repo(lease.repo_url, lease.ref, work_dir)
            
            # Convert job dict to Job model
            job = _dict_to_job(lease.job)
            
            # Create cache store
            cache = CacheStore(cache_root)
            
            # Execute job
            try:
                job_name, status = _run_job(job, repo_path, cache)
                job_results = {
                    "job_name": job_name,
                    "status": status,
                }
                execution_status = "success" if status != "failed" else "failed"
            except Exception as e:
                execution_status = "failed"
                job_results = {
                    "error": str(e),
                    "error_type": type(e).__name__,
                }
                # Log the error to capture buffer
                print(f"Error: {e}", file=sys.stderr)
                raise
        
        # Get final logs from capture buffer
        logs = log_capture.get_logs()
        
    except Exception as e:
        execution_status = "failed"
        error_msg = str(e)
        
        # Get logs from capture buffer (may be empty if error occurred before context)
        captured_logs = log_capture.get_logs()
        if captured_logs:
            logs = captured_logs
            # Append error message if not already in logs
            if error_msg not in logs:
                logs = f"{logs}\nError: {error_msg}"
        else:
            logs = error_msg
        
        job_results = {
            "error": error_msg,
            "error_type": type(e).__name__,
        }
    
    return ExecutionResult(
        status=execution_status,
        logs=logs,
        job_results=job_results,
        error=job_results.get("error"),
    )
