CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS runs (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  repo        TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('queued','running','ok','failed','canceled')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobs (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  run_id       UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  job_name     TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('queued','leased','running','ok','failed','canceled')),
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  logs         TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jobs_run_id ON jobs(run_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS leases (
  job_id      UUID PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
  agent_id    TEXT NOT NULL,
  leased_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leases_expires_at ON leases(expires_at);
