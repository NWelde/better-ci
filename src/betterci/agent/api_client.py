# agent/api_client.py
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import urljoin

from .models import Lease


class APIError(Exception):
    """Raised when API requests fail."""
    pass


class APIClient:
    """HTTP client for communicating with the BetterCI API."""
    
    def __init__(self, base_url: str, agent_id: str):
        """
        Initialize API client.
        
        Args:
            base_url: Base URL of the API (e.g., "https://api.example.com")
            agent_id: Unique identifier for this agent instance
        """
        # Ensure base_url doesn't end with /
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
    
    def _request(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> dict:
        """
        Make an HTTP request to the API.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (e.g., "/leases")
            data: Optional JSON data to send in request body
            headers: Optional additional headers
            
        Returns:
            Parsed JSON response as dictionary
            
        Raises:
            APIError: If the request fails
        """
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        
        req_headers = {
            "Content-Type": "application/json",
        }
        if headers:
            req_headers.update(headers)
        
        req_data = None
        if data is not None:
            req_data = json.dumps(data).encode("utf-8")
        
        req = urllib.request.Request(url, data=req_data, headers=req_headers, method=method)
        
        try:
            with urllib.request.urlopen(req) as response:
                response_data = response.read().decode("utf-8")
                if response_data:
                    return json.loads(response_data)
                return {}
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            # 204 No Content is used for "no jobs available"
            if e.code == 204:
                raise APIError("204")  # Special marker for no jobs
            raise APIError(f"API request failed: {e.code} {e.reason}. {error_body}")
        except urllib.error.URLError as e:
            raise APIError(f"Network error: {e.reason}")
        except json.JSONDecodeError as e:
            raise APIError(f"Invalid JSON response: {e}")
    
    def claim_lease(self) -> Optional[Lease]:
        """
        Claim an available job lease from the queue.
        
        Returns:
            Lease object if a job is available, None otherwise
        """
        try:
            response = self._request(
                "POST",
                "/leases/claim",
                data={"agent_id": self.agent_id},
            )
            # Validate required fields
            if not isinstance(response, dict):
                return None
            if "job_id" not in response or "job_name" not in response or "payload_json" not in response:
                return None
            return Lease.from_dict(response)
        except APIError as e:
            # 204 means no jobs available (this is normal)
            error_str = str(e)
            if "204" in error_str:
                return None
            # Re-raise other errors
            raise
        except (KeyError, TypeError, ValueError) as e:
            # Invalid response format
            return None
    
    def send_logs(self, job_id: str, logs: str) -> None:
        """
        Send log chunks to the API.
        
        Note: This method is deprecated. Logs are now batched and sent
        at job completion via complete_lease() in the details dict.
        This method is kept for backwards compatibility but does nothing.
        
        Args:
            job_id: ID of the job
            logs: Log content to send
        """
        # Logs are batched and sent at completion via complete_lease()
        # This method is a no-op for backwards compatibility
        pass
    
    def complete_lease(self, job_id: str, status: str, details: dict) -> None:
        """
        Mark a lease as complete and send final results.
        
        Args:
            job_id: ID of the job
            status: "ok" or "failed" (must match API expectations)
            details: Dictionary with execution details (logs, results, etc.)
        """
        # Convert "success" to "ok" if needed
        api_status = "ok" if status == "success" else status
        if api_status not in ("ok", "failed"):
            api_status = "failed"
        
        self._request(
            "POST",
            f"/leases/{job_id}/complete",
            data={
                "agent_id": self.agent_id,
                "status": api_status,
                "details": details,
            },
        )
