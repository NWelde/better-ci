from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime

class Base(DeclarativeBase):
    pass

class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()"))
    repo: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False)

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()"))
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    job_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False)

class Lease(Base):
    __tablename__ = "leases"
    job_id: Mapped[str] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    agent_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    leased_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
