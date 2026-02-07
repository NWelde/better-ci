# agent/models.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class Lease:
    """Represents a job lease from the API."""
    lease_id: str
    job: Dict[str, Any]  # Job definition from API (will be converted to Job model)
    repo_url: str
    ref: str
    # Additional fields that might come from API
    run_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Lease:
        """Create Lease from API response dictionary."""
        return cls(
            lease_id=data["lease_id"],
            job=data["job"],
            repo_url=data["repo_url"],
            ref=data.get("ref", "HEAD"),
            run_id=data.get("run_id"),
            metadata=data.get("metadata"),
        )


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
