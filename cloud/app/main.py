from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .db import SessionLocal, engine
from .models import Base, Run, Job, Lease
from .redisq import enqueue_job, dequeue_job, requeue_job, r, lease_lock_key
from .settings import LEASE_SECONDS

app = FastAPI(title="BetterCI Cloud Control Plane")

# -------------------- Schemas --------------------

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
    status: str  # ok|failed
    details: dict[str, Any] = Field(default_factory=dict)

class JobResponse(BaseModel):
    id: str
    job_name: str
    status: str
    logs: str | None
    created_at: datetime

# -------------------- Startup --------------------

@app.on_event("startup")
async def startup() -> None:
    # Creates tables if they don't exist. (You still need uuid-ossp extension via schema.sql.)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

# -------------------- Endpoints --------------------

@app.post("/runs", response_model=CreateRunResponse)
async def create_run(req: CreateRunRequest):
    job_ids: list[str] = []

    async with SessionLocal() as s:
        async with s.begin():
            run = Run(repo=req.repo, status="queued")
            s.add(run)
            await s.flush()

            for j in req.jobs:
                job = Job(run_id=run.id, job_name=j.job_name, status="queued", payload_json=j.payload_json)
                s.add(job)
                await s.flush()
                job_ids.append(str(job.id))

            run_id = str(run.id)

    # push to Redis after DB commit
    for jid in job_ids:
        await enqueue_job(jid)

    return CreateRunResponse(run_id=run_id, job_ids=job_ids)

@app.post("/leases/claim", response_model=ClaimedJob)
async def claim(req: ClaimRequest):
    job_id = await dequeue_job(timeout_s=5)
    if not job_id:
        raise HTTPException(status_code=204, detail="No jobs available")

    # Lock in Redis to reduce duplicate leasing during retries
    lock_key = lease_lock_key(job_id)
    got_lock = await r.set(lock_key, req.agent_id, nx=True, ex=LEASE_SECONDS)
    if not got_lock:
        return await claim(req)  # try again

    expires_at = now_utc() + timedelta(seconds=LEASE_SECONDS)

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
            if lease and lease.expires_at > now_utc():
                await r.delete(lock_key)
                await requeue_job(job_id)
                return await claim(req)

            if lease:
                lease.agent_id = req.agent_id
                lease.leased_at = now_utc()
                lease.expires_at = expires_at
            else:
                s.add(Lease(job_id=uuid.UUID(job_id), agent_id=req.agent_id, leased_at=now_utc(), expires_at=expires_at))

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

@app.post("/leases/{job_id}/complete")
async def complete(job_id: str, req: CompleteRequest):
    if req.status not in ("ok", "failed"):
        raise HTTPException(status_code=400, detail="status must be ok|failed")

    async with SessionLocal() as s:
        async with s.begin():
            job = await s.get(Job, uuid.UUID(job_id))
            if not job:
                raise HTTPException(status_code=404, detail="Job not found")

            lease = await s.get(Lease, uuid.UUID(job_id))
            if not lease:
                raise HTTPException(status_code=409, detail="No lease for job")
            if lease.agent_id != req.agent_id:
                raise HTTPException(status_code=403, detail="Lease owned by different agent")

            # Store logs from details if present
            logs = req.details.get("logs", "")
            job.logs = logs if logs else None
            
            job.status = req.status
            await s.delete(lease)

            run = await s.get(Run, job.run_id)
            if run:
                if req.status == "failed":
                    run.status = "failed"
                else:
                    q = sa.select(sa.func.count()).select_from(Job).where(
                        Job.run_id == job.run_id,
                        Job.status.not_in(["ok", "canceled"]),
                    )
                    remaining = (await s.execute(q)).scalar_one()
                    if remaining == 0 and run.status != "failed":
                        run.status = "ok"

    await r.delete(lease_lock_key(job_id))
    return {"ok": True}

@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """Get job details including logs."""
    async with SessionLocal() as s:
        job = await s.get(Job, uuid.UUID(job_id))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        return JobResponse(
            id=str(job.id),
            job_name=job.job_name,
            status=job.status,
            logs=job.logs,
            created_at=job.created_at,
        )
