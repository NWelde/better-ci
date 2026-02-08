"""Console output formatting utilities for BetterCI."""

from __future__ import annotations

import sys
from typing import Optional


class Console:
    """Centralized console output formatting."""
    
    def __init__(self, debug: bool = False):
        """
        Initialize console formatter.
        
        Args:
            debug: If True, show detailed output including stack traces
        """
        self.debug = debug
    
    def print_header(self, title: str) -> None:
        """Print a section header."""
        print(f"\n{title}")
        print("-" * len(title))
    
    def print_run_started(
        self,
        repository: str,
        workflow: str,
        job_count: int,
    ) -> None:
        """Print run start information."""
        print("\nRUN STARTED")
        print(f"Repository: {repository}")
        print(f"Workflow: {workflow}")
        print(f"Jobs: {job_count}")
        print()
    
    def print_job_start(self, name: str) -> None:
        """Print job start message."""
        print(f"\nJOB STARTED: {name}")
    
    def print_step(self, name: str) -> None:
        """Print step start message."""
        print(f"STEP: {name}")
    
    def print_success(self, name: str) -> None:
        """Print success message."""
        print(f"STATUS: success")
    
    def print_failure(
        self,
        name: str,
        reason: str,
        exit_code: Optional[int] = None,
        hint: Optional[str] = None,
        is_job: bool = False,
    ) -> None:
        """
        Print failure message.
        
        Args:
            name: Job or step name
            reason: Failure reason/error message
            exit_code: Optional exit code
            hint: Optional hint for user
            is_job: If True, print "JOB FAILED", otherwise "STEP FAILED"
        """
        prefix = "JOB FAILED" if is_job else "STEP FAILED"
        print(f"{prefix}: {name}")
        if exit_code is not None:
            print(f"Exit code: {exit_code}")
        if hint:
            print(f"Hint: {hint}")
        if self.debug:
            print(f"Error details: {reason}")
        else:
            # Show first line of error for non-debug mode
            error_line = reason.split('\n')[0] if reason else "Unknown error"
            if error_line and error_line != str(reason):
                print(f"Error: {error_line}")
    
    def print_cache_hit(self, job: str, reason: str) -> None:
        """Print cache hit message."""
        print(f"CACHE: hit ({reason})")
    
    def print_cache_miss(self, job: str) -> None:
        """Print cache miss message."""
        print(f"CACHE: miss")
    
    def print_cache_saved(self, job: str, key: str) -> None:
        """Print cache save message."""
        short_key = key[:12] + "..." if len(key) > 12 else key
        print(f"CACHE: saved ({short_key})")
    
    def print_job_skipped(self, name: str, reason: str) -> None:
        """Print job skipped message."""
        print(f"\nJOB STARTED: {name}")
        print(f"STATUS: skipped ({reason})")
    
    def print_plan_job(self, name: str, reason: str) -> None:
        """Print job selection plan."""
        print(f"  {name} ({reason})")
    
    def print_plan_job_skipped(self, name: str, reason: str) -> None:
        """Print job skipped in plan."""
        print(f"  {name} (skipped: {reason})")
    
    def print_results(self, results: dict[str, str]) -> None:
        """Print final results summary."""
        print("\n" + "=" * 40)
        print("RESULTS")
        print("=" * 40)
        for job, status in results.items():
            status_display = status.upper() if status != "ok" else "SUCCESS"
            print(f"  {job}: {status_display}")
    
    def print_error(
        self,
        title: str,
        message: str,
        details: Optional[list[str]] = None,
        suggestion: Optional[str] = None,
    ) -> None:
        """
        Print structured error message.
        
        Args:
            title: Error title
            message: Main error message
            details: Optional list of detail lines
            suggestion: Optional suggestion for user
        """
        print(f"\nERROR: {title}", file=sys.stderr)
        print(f"{message}", file=sys.stderr)
        if details:
            for detail in details:
                print(f"  {detail}", file=sys.stderr)
        if suggestion:
            print(f"\n{suggestion}", file=sys.stderr)
    
    def print_exception(self, exc: Exception) -> None:
        """Print exception, with full traceback only in debug mode."""
        if self.debug:
            import traceback
            traceback.print_exc()
        else:
            print(f"Error: {exc}", file=sys.stderr)
    
    def print_agent_started(
        self,
        agent_id: str,
        api: str,
        poll_interval: int,
    ) -> None:
        """Print agent start information."""
        print("\nAGENT STARTED")
        print(f"Agent ID: {agent_id}")
        print(f"API: {api}")
        print(f"Polling every: {poll_interval}s")
        print()
    
    def print_lease_acquired(self, job_name: str, run_id: str) -> None:
        """Print lease acquisition message."""
        print("\nLEASE ACQUIRED")
        print(f"Job: {job_name}")
        print(f"Run ID: {run_id}")
    
    def print_execution_complete(
        self,
        status: str,
        duration: Optional[float] = None,
    ) -> None:
        """Print execution completion message."""
        print("\nEXECUTION COMPLETE")
        print(f"Status: {status}")
        if duration is not None:
            print(f"Duration: {duration:.1f}s")
    
    def print_info(self, message: str) -> None:
        """Print informational message."""
        print(message)
    
    def print_debug(self, message: str) -> None:
        """Print debug message (only if debug mode enabled)."""
        if self.debug:
            print(f"[DEBUG] {message}", file=sys.stderr)


# Global console instance (will be initialized by CLI)
_console: Optional[Console] = None


def get_console() -> Console:
    """Get the global console instance."""
    global _console
    if _console is None:
        _console = Console()
    return _console


def set_console(console: Console) -> None:
    """Set the global console instance."""
    global _console
    _console = console

