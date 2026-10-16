-- Agent Registry schema (from s0/agent-registry-service/migrations 001-005)

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS agents (
  agent_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_type       VARCHAR(50) NOT NULL,
  owner_id         UUID NOT NULL,
  external_id      VARCHAR(255),
  status           VARCHAR(50) NOT NULL DEFAULT 'PENDING',
  passport_id      UUID,
  metadata         JSONB DEFAULT '{}',
  registration_ts  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_ts       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT agents_external_id_owner_unique UNIQUE (external_id, owner_id),
  CONSTRAINT agents_status_check CHECK (status IN ('PENDING','ACTIVE','SUSPENDED','DEREGISTERED')),
  CONSTRAINT agents_type_check CHECK (agent_type IN ('AUTONOMOUS','ASSISTED','SYSTEM','EPHEMERAL'))
);

CREATE INDEX IF NOT EXISTS idx_agents_owner_id ON agents(owner_id);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_agents_type ON agents(agent_type);
CREATE INDEX IF NOT EXISTS idx_agents_registration_ts ON agents(registration_ts);

CREATE TABLE IF NOT EXISTS agent_status_history (
  history_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id         UUID NOT NULL REFERENCES agents(agent_id),
  previous_status  VARCHAR(50),
  new_status       VARCHAR(50) NOT NULL,
  reason           TEXT NOT NULL,
  changed_by       VARCHAR(255) NOT NULL,
  changed_ts       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_status_history_agent_id ON agent_status_history(agent_id);

CREATE TABLE IF NOT EXISTS agent_lifecycle_outbox (
  outbox_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id         UUID NOT NULL,
  event_type       VARCHAR(50) NOT NULL,
  payload          JSONB NOT NULL,
  status           VARCHAR(20) NOT NULL DEFAULT 'PENDING',
  created_ts       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_attempt_ts  TIMESTAMPTZ,
  attempt_count    INT NOT NULL DEFAULT 0,
  CONSTRAINT outbox_status_check CHECK (status IN ('PENDING','DELIVERED','FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_outbox_status
  ON agent_lifecycle_outbox(status, created_ts)
  WHERE status = 'PENDING';

CREATE INDEX IF NOT EXISTS idx_agents_owner_agent ON agents(owner_id, agent_id);

ALTER TABLE agents
  ADD COLUMN IF NOT EXISTS admission_constraints JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS admitted_policy_id TEXT,
  ADD COLUMN IF NOT EXISTS admitted_policy_ver TEXT,
  ADD COLUMN IF NOT EXISTS admission_ts TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE IF NOT EXISTS admission_envelopes (
    envelope_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL UNIQUE,
    policy_id TEXT,
    policy_ver TEXT,
    admitted_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    admitted_behavioral_caps JSONB NOT NULL DEFAULT '{}'::jsonb,
    admitted_ts TIMESTAMPTZ NOT NULL,
    issuing_authority TEXT NOT NULL DEFAULT 'agent-registry',
    signature BYTEA NOT NULL
);

CREATE INDEX IF NOT EXISTS admission_envelopes_agent_id_idx ON admission_envelopes (agent_id);
