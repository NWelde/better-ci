from __future__ import annotations
import os

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]
QUEUE_NAME = os.environ.get("QUEUE_NAME", "betterci:queue")
LEASE_SECONDS = int(os.environ.get("LEASE_SECONDS", "600"))
