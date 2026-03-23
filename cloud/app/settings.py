from __future__ import annotations
import os

DATABASE_URL  = os.environ["DATABASE_URL"]
REDIS_URL     = os.environ["REDIS_URL"]
QUEUE_NAME    = os.environ.get("QUEUE_NAME", "betterci:queue")
LEASE_SECONDS = int(os.environ.get("LEASE_SECONDS", "600"))

# Optional API key for authentication.
# When set, all mutating endpoints (POST /runs, POST /leases/claim,
# POST /leases/{id}/complete) require the header: X-API-Key: <value>.
# Leave unset (or empty) to disable authentication (development mode).
API_KEY: str | None = os.environ.get("BETTERCI_API_KEY") or None
