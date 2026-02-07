from __future__ import annotations

import redis.asyncio as redis
from .settings import REDIS_URL, QUEUE_NAME

r = redis.from_url(REDIS_URL, decode_responses=True)

def lease_lock_key(job_id: str) -> str:
    return f"betterci:lease_lock:{job_id}"

async def enqueue_job(job_id: str) -> None:
    await r.rpush(QUEUE_NAME, job_id)  # FIFO: push right

async def dequeue_job(timeout_s: int = 5) -> str | None:
    item = await r.blpop(QUEUE_NAME, timeout=timeout_s)  # FIFO: pop left
    if not item:
        return None
    _q, job_id = item
    return job_id

async def requeue_job(job_id: str) -> None:
    await r.lpush(QUEUE_NAME, job_id)
