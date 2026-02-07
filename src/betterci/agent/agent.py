# agent/agent.py
from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

from .api_client import APIClient, APIError
from .executor import execute_lease
from .models import Lease


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
        print(f"\nReceived signal {signum}, shutting down gracefully...")
        self.running = False
    
    def run(self) -> None:
        """Run the agent loop."""
        print(f"BetterCI agent starting...")
        print(f"  API: {self.api_client.base_url}")
        print(f"  Poll interval: {self.poll_interval}s")
        print(f"  Work directory: {self.work_dir}")
        print()
        
        while self.running:
            try:
                lease = self.api_client.claim_lease()
                
                if lease:
                    print(f"[{lease.job_id}] Acquired lease for job: {lease.job_name}")
                    self._execute_lease(lease)
                else:
                    # No jobs available, wait before next poll
                    time.sleep(self.poll_interval)
                    
            except KeyboardInterrupt:
                print("\nInterrupted by user")
                break
            except APIError as e:
                print(f"API error: {e}")
                # Wait before retrying on API errors
                time.sleep(self.poll_interval)
            except Exception as e:
                print(f"Unexpected error: {e}")
                # Wait before retrying
                time.sleep(self.poll_interval)
        
        print("Agent stopped.")
    
    def _execute_lease(self, lease: Lease) -> None:
        """Execute a single lease."""
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
            
            print(f"[{lease.job_id}] Completed with status: {result.status}")
            
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
                print(f"[{lease.job_id}] Failed to send completion: {api_err}")
            
            print(f"[{lease.job_id}] Execution failed: {e}")


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
