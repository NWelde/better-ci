from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import sqlalchemy as sa
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .db import SessionLocal, engine
from .models import Base, Run, Job, Lease
from .redisq import enqueue_job, dequeue_job, requeue_job, r, lease_lock_key
from .settings import LEASE_SECONDS, API_KEY

app = FastAPI(
    title="BetterCI Cloud Control Plane",
    description=(
        "Queues workflow runs, distributes jobs to agents, and stores logs. "
        "Set BETTERCI_API_KEY to enable authentication."
    ),
)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

async def require_api_key(request: Request) -> None:
    """
    Optional API key guard. Enforced only when BETTERCI_API_KEY is configured.
    Reads the key from the X-API-Key header.
    """
    if not API_KEY:
        return  # Auth disabled — development / self-hosted without a key
    provided = request.headers.get("X-API-Key", "")
    if provided != API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized. Provide a valid API key in the X-API-Key header.",
        )


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class CreateRunJob(BaseModel):
    job_name: str
    payload_json: dict[str, Any] = Field(default_factory=dict)


class CreateRunRequest(BaseModel):
    repo: str
    jobs: list[CreateRunJob]


class CreateRunResponse(BaseModel):
    run_id: str
    job_ids: list[str]


class ClaimRequest(BaseModel):
    agent_id: str


class ClaimedJob(BaseModel):
    job_id: str
    run_id: str
    job_name: str
    payload_json: dict[str, Any]
    lease_expires_at: str


class CompleteRequest(BaseModel):
    agent_id: str
    status: str   # "ok" | "failed"
    details: dict[str, Any] = Field(default_factory=dict)


class JobResponse(BaseModel):
    id: str
    job_name: str
    status: str
    logs: Optional[str]
    created_at: datetime


class RunResponse(BaseModel):
    run_id: str
    repo: str
    status: str
    created_at: datetime
    jobs: list[JobResponse]


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Start background task for expired lease requeue
    asyncio.create_task(_requeue_expired_leases_loop())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Background: expired lease requeue
# ---------------------------------------------------------------------------

async def _requeue_expired_leases_loop() -> None:
    """
    Periodically scan for leases that have expired (agent died / timed out)
    and re-queue those jobs so another agent can pick them up.

    Runs every 30 seconds.
    """
    while True:
        await asyncio.sleep(30)
        try:
            await _requeue_expired_leases()
        except Exception:
            pass  # Never crash the server over a background task


async def _requeue_expired_leases() -> None:
    async with SessionLocal() as s:
        async with s.begin():
            now = _now()
            result = await s.execute(
                sa.select(Lease).where(Lease.expires_at <= now)
            )
            expired: list[Lease] = list(result.scalars().all())

            for lease in expired:
                job = await s.get(Job, lease.job_id)
                if job and job.status == "leased":
                    job.status = "queued"
                    await s.delete(lease)
                    await requeue_job(str(lease.job_id))
                    # Clear the Redis lock so a new agent can claim it
                    await r.delete(lease_lock_key(str(lease.job_id)))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post(
    "/runs",
    response_model=CreateRunResponse,
    dependencies=[Depends(require_api_key)],
)
async def create_run(req: CreateRunRequest):
    """Queue a new workflow run and enqueue all its jobs."""
    job_ids: list[str] = []

    async with SessionLocal() as s:
        async with s.begin():
            run = Run(repo=req.repo, status="queued")
            s.add(run)
            await s.flush()

            for j in req.jobs:
                job = Job(
                    run_id=run.id,
                    job_name=j.job_name,
                    status="queued",
                    payload_json=j.payload_json,
                )
                s.add(job)
                await s.flush()
                job_ids.append(str(job.id))

            run_id = str(run.id)

    for jid in job_ids:
        await enqueue_job(jid)

    return CreateRunResponse(run_id=run_id, job_ids=job_ids)


@app.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str):
    """Get the status and all job results for a run."""
    async with SessionLocal() as s:
        try:
            run = await s.get(Run, uuid.UUID(run_id))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="Invalid run_id format")

        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        result = await s.execute(
            sa.select(Job).where(Job.run_id == run.id).order_by(Job.created_at)
        )
        jobs = list(result.scalars().all())

        return RunResponse(
            run_id=str(run.id),
            repo=run.repo,
            status=run.status,
            created_at=run.created_at,
            jobs=[
                JobResponse(
                    id=str(j.id),
                    job_name=j.job_name,
                    status=j.status,
                    logs=j.logs,
                    created_at=j.created_at,
                )
                for j in jobs
            ],
        )


@app.post(
    "/leases/claim",
    response_model=ClaimedJob,
    dependencies=[Depends(require_api_key)],
)
async def claim(req: ClaimRequest):
    """Agent claims the next available job from the queue."""
    job_id = await dequeue_job(timeout_s=5)
    if not job_id:
        raise HTTPException(status_code=204, detail="No jobs available")

    lock_key = lease_lock_key(job_id)
    got_lock = await r.set(lock_key, req.agent_id, nx=True, ex=LEASE_SECONDS)
    if not got_lock:
        # Another agent won the race — try again
        return await claim(req)

    expires_at = _now() + timedelta(seconds=LEASE_SECONDS)

    async with SessionLocal() as s:
        async with s.begin():
            job = await s.get(Job, uuid.UUID(job_id))
            if not job:
                await r.delete(lock_key)
                raise HTTPException(status_code=404, detail="Job not found")

            if job.status in ("ok", "failed", "canceled"):
                await r.delete(lock_key)
                raise HTTPException(status_code=409, detail=f"Job already {job.status}")

            lease = await s.get(Lease, uuid.UUID(job_id))
            if lease and lease.expires_at > _now():
                await r.delete(lock_key)
                await requeue_job(job_id)
                return await claim(req)

            if lease:
                lease.agent_id = req.agent_id
                lease.leased_at = _now()
                lease.expires_at = expires_at
            else:
                s.add(Lease(
                    job_id=uuid.UUID(job_id),
                    agent_id=req.agent_id,
                    leased_at=_now(),
                    expires_at=expires_at,
                ))

            job.status = "leased"

            run = await s.get(Run, job.run_id)
            if run and run.status == "queued":
                run.status = "running"

            return ClaimedJob(
                job_id=job_id,
                run_id=str(job.run_id),
                job_name=job.job_name,
                payload_json=job.payload_json,
                lease_expires_at=expires_at.isoformat(),
            )


@app.post(
    "/leases/{job_id}/complete",
    dependencies=[Depends(require_api_key)],
)
async def complete(job_id: str, req: CompleteRequest):
    """Agent reports job completion with logs and status."""
    if req.status not in ("ok", "failed"):
        raise HTTPException(status_code=400, detail="status must be 'ok' or 'failed'")

    async with SessionLocal() as s:
        async with s.begin():
            try:
                job = await s.get(Job, uuid.UUID(job_id))
            except (ValueError, AttributeError):
                raise HTTPException(status_code=400, detail="Invalid job_id format")

            if not job:
                raise HTTPException(status_code=404, detail="Job not found")

            lease = await s.get(Lease, uuid.UUID(job_id))
            if not lease:
                raise HTTPException(status_code=409, detail="No active lease for this job")
            if lease.agent_id != req.agent_id:
                raise HTTPException(
                    status_code=403,
                    detail=f"Lease is owned by agent '{lease.agent_id}', not '{req.agent_id}'",
                )

            logs = req.details.get("logs", "") or ""
            job.logs = logs if logs else None
            job.status = req.status
            await s.delete(lease)

            run = await s.get(Run, job.run_id)
            if run:
                if req.status == "failed":
                    run.status = "failed"
                else:
                    remaining_q = sa.select(sa.func.count()).select_from(Job).where(
                        Job.run_id == job.run_id,
                        Job.status.not_in(["ok", "canceled"]),
                    )
                    remaining = (await s.execute(remaining_q)).scalar_one()
                    if remaining == 0 and run.status != "failed":
                        run.status = "ok"

    await r.delete(lease_lock_key(job_id))
    return {"ok": True}


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """Get a job's status and logs by ID."""
    async with SessionLocal() as s:
        try:
            job = await s.get(Job, uuid.UUID(job_id))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="Invalid job_id format")

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        return JobResponse(
            id=str(job.id),
            job_name=job.job_name,
            status=job.status,
            logs=job.logs,
            created_at=job.created_at,
        )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check endpoint for load balancers and monitoring."""
    return {"status": "ok"}
