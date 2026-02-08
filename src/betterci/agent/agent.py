# agent/agent.py
from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

from .api_client import APIClient, APIError
from .executor import execute_lease
from .models import Lease
from betterci.ui.console import get_console


class Agent:
    """BetterCI agent that polls for jobs and executes them."""
    
    def __init__(self, api_url: str, agent_id: str, poll_interval: int = 5):
        """
        Initialize agent.
        
        Args:
            api_url: Base URL of the API
            agent_id: Unique identifier for this agent instance
            poll_interval: Seconds to wait between polls when no jobs available
        """
        self.api_client = APIClient(api_url, agent_id)
        self.poll_interval = poll_interval
        self.work_dir = Path(".betterci/agent_work")
        self.cache_root = Path(".betterci/cache")
        self.running = True
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        console = get_console()
        console.print_info(f"\nReceived signal {signum}, shutting down gracefully...")
        self.running = False
    
    def run(self) -> None:
        """Run the agent loop."""
        console = get_console()
        console.print_agent_started(
            agent_id=self.api_client.agent_id,
            api=self.api_client.base_url,
            poll_interval=self.poll_interval,
        )
        
        while self.running:
            try:
                lease = self.api_client.claim_lease()
                
                if lease:
                    console.print_lease_acquired(
                        job_name=lease.job_name,
                        run_id=lease.job_id,
                    )
                    self._execute_lease(lease)
                else:
                    # No jobs available, wait before next poll
                    time.sleep(self.poll_interval)
                    
            except KeyboardInterrupt:
                console.print_info("\nInterrupted by user")
                break
            except APIError as e:
                console.print_error(
                    "API error",
                    str(e),
                    suggestion="Check API connectivity and retry.",
                )
                # Wait before retrying on API errors
                time.sleep(self.poll_interval)
            except Exception as e:
                console.print_exception(e)
                # Wait before retrying
                time.sleep(self.poll_interval)
        
        console.print_info("Agent stopped.")
    
    def _execute_lease(self, lease: Lease) -> None:
        """Execute a single lease."""
        import time
        console = get_console()
        start_time = time.time()
        
        try:
            result = execute_lease(
                lease,
                self.api_client,
                self.work_dir,
                self.cache_root,
            )
            
            # Send completion status
            self.api_client.complete_lease(
                lease.job_id,
                result.status,
                {
                    "logs": result.logs,
                    "results": result.job_results,
                    "error": result.error,
                },
            )
            
            duration = time.time() - start_time
            console.print_execution_complete(
                status=result.status,
                duration=duration,
            )
            
            # Show logs in debug mode
            if console.debug and result.logs:
                console.print_info(f"\nLogs for {lease.job_name}:")
                console.print_info("=" * 60)
                console.print_info(result.logs)
                console.print_info("=" * 60)
            
        except Exception as e:
            # Send failure status
            try:
                self.api_client.complete_lease(
                    lease.job_id,
                    "failed",
                    {
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                )
            except Exception as api_err:
                console.print_error(
                    "Failed to send completion",
                    f"Could not send completion status to API: {api_err}",
                )
            
            duration = time.time() - start_time
            console.print_execution_complete(
                status="failed",
                duration=duration,
            )
            console.print_exception(e)


def run_agent(api_url: str, agent_id: str, poll_interval: int = 5) -> None:
    """
    Run the BetterCI agent loop.
    
    Args:
        api_url: Base URL of the API
        agent_id: Unique identifier for this agent instance
        poll_interval: Seconds to wait between polls when no jobs available
    """
    agent = Agent(api_url, agent_id, poll_interval)
    agent.run()
