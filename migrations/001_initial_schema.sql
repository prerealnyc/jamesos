-- JAMES OS — initial schema
-- Memory substrate: events + plug_ins + adapters + queries + actions + outbox.
-- Multi-tenant via tenant_id everywhere. RLS policies enforce isolation.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS vector;

-- Tenants. One row per company using JAMES OS.
CREATE TABLE IF NOT EXISTS tenants (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL,
  config      jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Seed the default tenant for v1 single-tenant operation.
INSERT INTO tenants (id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'Tenant Zero')
ON CONFLICT (id) DO NOTHING;

-- Users. Maps to Supabase Auth in production; standalone here.
CREATE TABLE IF NOT EXISTS users (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  email       text UNIQUE,
  role        text NOT NULL DEFAULT 'operator',
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- The event log. Append-only. Every input becomes an event.
-- Supersession via parent_event_id / superseded_by gives time-travel + audit.
CREATE TABLE IF NOT EXISTS events (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id)
                  DEFAULT current_setting('app.current_tenant', true)::uuid,
  event_type      text NOT NULL,
  payload         jsonb NOT NULL,
  raw_content     text,
  embedding       vector(1024),
  embedding_model text,
  source          jsonb NOT NULL DEFAULT '{}'::jsonb,
  participants    uuid[] NOT NULL DEFAULT ARRAY[]::uuid[],
  entities        text[] NOT NULL DEFAULT ARRAY[]::text[],
  parent_event_id uuid REFERENCES events(id),
  superseded_by   uuid REFERENCES events(id),
  effective_at    timestamptz NOT NULL DEFAULT now(),
  expires_at      timestamptz,
  confidence      real NOT NULL DEFAULT 1.0,
  created_at      timestamptz NOT NULL DEFAULT now(),
  metadata        jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- Vector index for semantic search.
CREATE INDEX IF NOT EXISTS events_embedding_idx
  ON events USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

-- Full-text index for keyword search.
CREATE INDEX IF NOT EXISTS events_fts_idx
  ON events USING gin (to_tsvector('english', coalesce(raw_content, '')));

-- Structured indexes for common filters.
CREATE INDEX IF NOT EXISTS events_tenant_type_time_idx
  ON events (tenant_id, event_type, effective_at DESC);
CREATE INDEX IF NOT EXISTS events_tenant_active_idx
  ON events (tenant_id, effective_at DESC)
  WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS events_dedupe_idx
  ON events ((source ->> 'dedupe_key'))
  WHERE source ? 'dedupe_key';

-- Plug-ins: frameworks, frustrations, guidelines, protocols.
-- Loaded into the system prompt at query time. Versioned so they can evolve.
CREATE TABLE IF NOT EXISTS plug_ins (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id)
               DEFAULT current_setting('app.current_tenant', true)::uuid,
  slot         text NOT NULL CHECK (slot IN ('framework', 'guideline', 'protocol', 'frustration', 'identity')),
  name         text NOT NULL,
  content      jsonb NOT NULL,
  applies_to   text[] NOT NULL DEFAULT ARRAY[]::text[],
  version      int NOT NULL DEFAULT 1,
  active       boolean NOT NULL DEFAULT true,
  created_by   uuid REFERENCES users(id),
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS plug_ins_tenant_active_idx
  ON plug_ins (tenant_id, slot, active);

-- Adapters: ingestion source configurations per tenant.
CREATE TABLE IF NOT EXISTS adapters (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id)
                DEFAULT current_setting('app.current_tenant', true)::uuid,
  adapter_type  text NOT NULL,
  config        jsonb NOT NULL DEFAULT '{}'::jsonb,
  secret_ref    text,
  schedule      text,
  last_run_at   timestamptz,
  last_status   text,
  active        boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS adapters_tenant_idx ON adapters (tenant_id, active);

-- Every query and its answer is logged (the OS remembers what was asked).
CREATE TABLE IF NOT EXISTS queries (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id)
                      DEFAULT current_setting('app.current_tenant', true)::uuid,
  user_id             uuid REFERENCES users(id),
  question            text NOT NULL,
  retrieved_event_ids uuid[] NOT NULL DEFAULT ARRAY[]::uuid[],
  response            text,
  citations           jsonb NOT NULL DEFAULT '[]'::jsonb,
  confidence          real,
  refused             boolean NOT NULL DEFAULT false,
  refusal_reason      text,
  model               text,
  latency_ms          integer,
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS queries_tenant_time_idx ON queries (tenant_id, created_at DESC);

-- Proposed outbound actions wait here for approval.
CREATE TABLE IF NOT EXISTS actions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id)
                  DEFAULT current_setting('app.current_tenant', true)::uuid,
  proposed_by     text NOT NULL,
  action_type     text NOT NULL,
  payload         jsonb NOT NULL,
  status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'executed', 'failed')),
  approver_id     uuid REFERENCES users(id),
  approval_reason text,
  rejection_reason_code text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  decided_at      timestamptz,
  executed_at     timestamptz
);
CREATE INDEX IF NOT EXISTS actions_tenant_status_idx ON actions (tenant_id, status, created_at DESC);

-- Outbox for reliable side effects (embed, index, notify, execute_action).
CREATE TABLE IF NOT EXISTS outbox (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id)
               DEFAULT current_setting('app.current_tenant', true)::uuid,
  task_type    text NOT NULL,
  payload      jsonb NOT NULL,
  attempts     int NOT NULL DEFAULT 0,
  next_run_at  timestamptz NOT NULL DEFAULT now(),
  status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'done', 'failed')),
  last_error   text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS outbox_pending_idx
  ON outbox (next_run_at, tenant_id)
  WHERE status = 'pending';

-- Row-level security. Application code never filters by tenant_id —
-- Postgres does, via the app.current_tenant setting.
-- Production: this setting comes from the verified JWT claim.
ALTER TABLE tenants    ENABLE ROW LEVEL SECURITY;
ALTER TABLE users      ENABLE ROW LEVEL SECURITY;
ALTER TABLE events     ENABLE ROW LEVEL SECURITY;
ALTER TABLE plug_ins   ENABLE ROW LEVEL SECURITY;
ALTER TABLE adapters   ENABLE ROW LEVEL SECURITY;
ALTER TABLE queries    ENABLE ROW LEVEL SECURITY;
ALTER TABLE actions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox     ENABLE ROW LEVEL SECURITY;

-- Idempotent policy creation (CREATE POLICY has no IF NOT EXISTS).
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'tenants', 'users', 'events', 'plug_ins',
    'adapters', 'queries', 'actions', 'outbox'
  ] LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I_tenant ON %I', t, t);
    IF t = 'tenants' THEN
      EXECUTE format(
        'CREATE POLICY %I_tenant ON %I USING '
        '(id = current_setting(''app.current_tenant'', true)::uuid)', t, t);
    ELSE
      EXECUTE format(
        'CREATE POLICY %I_tenant ON %I USING '
        '(tenant_id = current_setting(''app.current_tenant'', true)::uuid)', t, t);
    END IF;
  END LOOP;
END $$;

-- NOTE: this file used to grant the local 'james_os' role BYPASSRLS
-- "for convenience". That was the same hole as production (a connecting
-- role exempt from RLS makes every policy above decorative).
-- 034_rls_enforcement.sql revokes the bypass and adds FORCE ROW LEVEL
-- SECURITY so the policies bind the table owner too. Do not re-add
-- BYPASSRLS here.
