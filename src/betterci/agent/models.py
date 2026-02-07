# agent/models.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class Lease:
    """Represents a job lease from the API (ClaimedJob response)."""
    job_id: str
    run_id: str
    job_name: str
    payload_json: Dict[str, Any]  # Contains job definition and repo info
    lease_expires_at: str  # ISO format timestamp

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Lease:
        """Create Lease from API ClaimedJob response dictionary."""
        return cls(
            job_id=data["job_id"],
            run_id=data["run_id"],
            job_name=data["job_name"],
            payload_json=data["payload_json"],
            lease_expires_at=data["lease_expires_at"],
        )
    
    @property
    def repo_url(self) -> str:
        """Extract repo URL from payload_json."""
        return self.payload_json.get("repo_url", "")
    
    @property
    def ref(self) -> str:
        """Extract git ref from payload_json."""
        return self.payload_json.get("ref", "HEAD")
    
    @property
    def job(self) -> Dict[str, Any]:
        """Extract job definition from payload_json."""
        # The job definition should be in payload_json
        return self.payload_json.get("job", self.payload_json)


@dataclass
class ExecutionResult:
    """Result of executing a job lease."""
    status: str  # "success" | "failed"
    logs: str
    job_results: Dict[str, Any]
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API submission."""
        return {
            "status": self.status,
            "logs": self.logs,
            "results": self.job_results,
            "error": self.error,
        }
