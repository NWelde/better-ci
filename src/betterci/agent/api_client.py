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
    
    def __init__(self, base_url: str, token: str):
        """
        Initialize API client.
        
        Args:
            base_url: Base URL of the API (e.g., "https://api.example.com")
            token: Authentication token
        """
        # Ensure base_url doesn't end with /
        self.base_url = base_url.rstrip("/")
        self.token = token
    
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
            "Authorization": f"Bearer {self.token}",
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
            raise APIError(f"API request failed: {e.code} {e.reason}. {error_body}")
        except urllib.error.URLError as e:
            raise APIError(f"Network error: {e.reason}")
        except json.JSONDecodeError as e:
            raise APIError(f"Invalid JSON response: {e}")
    
    def get_lease(self) -> Optional[Lease]:
        """
        Poll for an available job lease.
        
        Returns:
            Lease object if a job is available, None otherwise
        """
        try:
            response = self._request("GET", "/leases")
            # Handle empty response or null
            if not response or response is None:
                return None
            # Validate required fields
            if not isinstance(response, dict):
                return None
            if "lease_id" not in response or "job" not in response or "repo_url" not in response:
                return None
            return Lease.from_dict(response)
        except APIError as e:
            # If 404, no lease available (this is normal)
            error_str = str(e)
            if "404" in error_str:
                return None
            # Re-raise other errors
            raise
        except (KeyError, TypeError, ValueError) as e:
            # Invalid response format
            return None
    
    def send_logs(self, lease_id: str, logs: str) -> None:
        """
        Send log chunks to the API.
        
        Args:
            lease_id: ID of the lease
            logs: Log content to send
        """
        self._request(
            "POST",
            f"/leases/{lease_id}/logs",
            data={"logs": logs},
        )
    
    def complete_lease(self, lease_id: str, status: str, results: dict) -> None:
        """
        Mark a lease as complete and send final results.
        
        Args:
            lease_id: ID of the lease
            status: "success" or "failed"
            results: Dictionary with execution results
        """
        self._request(
            "POST",
            f"/leases/{lease_id}/complete",
            data={
                "status": status,
                **results,
            },
        )
