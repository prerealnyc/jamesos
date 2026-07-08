-- P1 of the bm2.0 merge (docs/unification-plan.md): the manager substrate.
-- Four landings: the append-only profile_fields envelope (bm2.0's core IP —
-- provenance, computed confidence, contradiction states; never flattened),
-- action_items (every suggestion from every eye as a trackable card),
-- job_runs (bookkeeping around every manager agent execution, with token
-- accounting for the credit meter), and the D5 status vocabulary extension
-- on the actions approval queue (published/measured close the loop).

-- ── profile_fields — append-only EAV envelope (D1/D2) ─────────────────────
CREATE TABLE IF NOT EXISTS profile_fields (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id)
                DEFAULT current_setting('app.current_tenant', true)::uuid,
  section       text NOT NULL,        -- identity|audience|voice|... (contracts.SECTIONS)
  field_key     text NOT NULL,        -- e.g. 'identity.tagline'
  item_key      text,                 -- list fields: one row per item (D1)
  value         jsonb NOT NULL DEFAULT '{}',   -- {"v": <the fact>}
  source        text NOT NULL,        -- user_stated|negotiated|audited|researched|inferred|derived|queue_signal
  confidence    real NOT NULL DEFAULT 0,       -- computed (D2), never model-reported
  citations     jsonb NOT NULL DEFAULT '[]',
  status        text NOT NULL DEFAULT 'unconfirmed'
                CHECK (status IN ('unconfirmed','confirmed','contradicted','stale','superseded')),
  version       int  NOT NULL DEFAULT 1,
  superseded_by uuid,                 -- newer row that replaced this one
  updated_by    text NOT NULL DEFAULT '',      -- agent/user that wrote it
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS profile_fields_current_idx
  ON profile_fields (tenant_id, field_key, item_key, status);
CREATE INDEX IF NOT EXISTS profile_fields_section_idx
  ON profile_fields (tenant_id, section, status);
ALTER TABLE profile_fields ENABLE ROW LEVEL SECURITY;
ALTER TABLE profile_fields FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS profile_fields_tenant ON profile_fields;
CREATE POLICY profile_fields_tenant ON profile_fields USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- ── action_items — follow-up cards from every eye/agent ───────────────────
CREATE TABLE IF NOT EXISTS action_items (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id)
                   DEFAULT current_setting('app.current_tenant', true)::uuid,
  kind             text NOT NULL,     -- collab|radar|trend|press|question|appearance|goal|promote|...
  title            text NOT NULL,
  detail           text NOT NULL DEFAULT '',
  status           text NOT NULL DEFAULT 'suggested'
                   CHECK (status IN ('suggested','active','done','dismissed','snoozed')),
  related_peer     text,              -- peer handle/roster ref when peer-scoped
  meta             jsonb NOT NULL DEFAULT '{}',
  updates          jsonb NOT NULL DEFAULT '[]',  -- [{ts, note, actor}]
  dedupe_key       text NOT NULL DEFAULT '',
  snooze_until     timestamptz,
  last_activity_at timestamptz NOT NULL DEFAULT now(),
  created_at       timestamptz NOT NULL DEFAULT now()
);
-- dedupe is per tenant; empty keys are exempt (partial unique index)
CREATE UNIQUE INDEX IF NOT EXISTS action_items_dedupe_idx
  ON action_items (tenant_id, dedupe_key) WHERE dedupe_key <> '';
CREATE INDEX IF NOT EXISTS action_items_tenant_idx
  ON action_items (tenant_id, status, last_activity_at DESC);
ALTER TABLE action_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE action_items FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS action_items_tenant ON action_items;
CREATE POLICY action_items_tenant ON action_items USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- ── daily_digests — the 'chunk for the day', one per tenant per date ──────
CREATE TABLE IF NOT EXISTS daily_digests (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id)
              DEFAULT current_setting('app.current_tenant', true)::uuid,
  date        date NOT NULL,
  summary     text NOT NULL DEFAULT '',
  items       jsonb NOT NULL DEFAULT '[]',
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS daily_digests_tenant_date_idx
  ON daily_digests (tenant_id, date);
ALTER TABLE daily_digests ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_digests FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS daily_digests_tenant ON daily_digests;
CREATE POLICY daily_digests_tenant ON daily_digests USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- ── job_runs — bookkeeping around every manager agent execution ───────────
-- Distinct from agent_runs (the tool-loop transcript store): job_runs records
-- scheduled/triggered manager jobs (eyes, brain, audits) with token accounting
-- that feeds the existing credit meter.
CREATE TABLE IF NOT EXISTS job_runs (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id)
              DEFAULT current_setting('app.current_tenant', true)::uuid,
  agent       text NOT NULL,          -- researcher|auditor|trends|strategist|...
  trigger     text NOT NULL DEFAULT 'manual',   -- manual|scheduled|cascade
  status      text NOT NULL DEFAULT 'running'
              CHECK (status IN ('running','succeeded','failed')),
  input       jsonb NOT NULL DEFAULT '{}',
  output      jsonb NOT NULL DEFAULT '{}',
  error       text NOT NULL DEFAULT '',
  tokens_in   int NOT NULL DEFAULT 0,
  tokens_out  int NOT NULL DEFAULT 0,
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS job_runs_tenant_idx
  ON job_runs (tenant_id, agent, started_at DESC);
ALTER TABLE job_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS job_runs_tenant ON job_runs;
CREATE POLICY job_runs_tenant ON job_runs USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- ── actions queue: D5 status vocabulary extension ──────────────────────────
-- The bm2.0 work-order lifecycle merges into the actions approval queue.
-- Existing states keep their meaning; the new ones let a single queue carry
-- an order from draft to measured impact (queued→generating→review→
-- pending_approval→approved→scheduled→published→measured, plus superseded/
-- cancelled). Transitions are validated in code (manager/state_machine.py).
ALTER TABLE actions DROP CONSTRAINT IF EXISTS actions_status_check;
ALTER TABLE actions ADD CONSTRAINT actions_status_check CHECK (status IN (
  'pending','approved','rejected','executed','failed',
  'queued','generating','review','pending_approval','scheduled',
  'published','measured','superseded','cancelled'
));

-- ── brand_questions: contradiction hook (D1) ───────────────────────────────
-- A profile-field contradiction materializes ONE open question; field_key
-- lets the dedupe and the eventual answer land back on the right envelope key.
ALTER TABLE brand_questions ADD COLUMN IF NOT EXISTS field_key text NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS brand_questions_field_key_idx
  ON brand_questions (tenant_id, field_key) WHERE field_key <> '';
