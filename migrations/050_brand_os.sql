-- Brand OS core: identity, scheduler, suggestions. (Roadmap M1 — built as
-- product, not demo scaffolding.)

-- 1. Who the brand IS — the profile every engine reads (voice, ideation,
--    Ask, strategy). One row per tenant.
CREATE TABLE IF NOT EXISTS brand_profiles (
  tenant_id    uuid PRIMARY KEY REFERENCES tenants(id)
               DEFAULT current_setting('app.current_tenant', true)::uuid,
  kind         text NOT NULL DEFAULT 'person',   -- person|asset|institution|politician
  identity     jsonb NOT NULL DEFAULT '{}',      -- who/mission/positioning
  goals        jsonb NOT NULL DEFAULT '[]',      -- ranked, measurable
  pillars      jsonb NOT NULL DEFAULT '[]',      -- topic mix targets
  taboos       jsonb NOT NULL DEFAULT '[]',
  platforms    jsonb NOT NULL DEFAULT '[]',
  peers        jsonb NOT NULL DEFAULT '[]',
  constraints  jsonb NOT NULL DEFAULT '{}',
  intake_done  boolean NOT NULL DEFAULT false,
  updated_at   timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE brand_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE brand_profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS brand_profiles_tenant ON brand_profiles;
CREATE POLICY brand_profiles_tenant ON brand_profiles USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- 2. Recurring platform jobs (daily research, playbook refresh, press scan…).
--    DELIBERATELY NO RLS: the scheduler loop is a platform-level worker that
--    must see every tenant's due jobs in one query. No user-facing endpoint
--    reads this table unscoped; management endpoints filter by the session
--    tenant explicitly.
CREATE TABLE IF NOT EXISTS scheduled_jobs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  kind          text NOT NULL,                    -- registry key
  cadence_hours int  NOT NULL DEFAULT 24,
  config        jsonb NOT NULL DEFAULT '{}',
  enabled       boolean NOT NULL DEFAULT true,
  last_run_at   timestamptz,
  last_status   text,                             -- ok|failed
  last_error    text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, kind)
);
CREATE INDEX IF NOT EXISTS scheduled_jobs_due_idx
  ON scheduled_jobs (enabled, last_run_at);

-- 3. Content suggestions — what the brand manager proposes on its own
--    (daily research today; press monitor and prescriptions later).
CREATE TABLE IF NOT EXISTS content_suggestions (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id)
              DEFAULT current_setting('app.current_tenant', true)::uuid,
  source      text NOT NULL DEFAULT 'daily_research',
  title       text NOT NULL,
  topic       text NOT NULL,
  format      text NOT NULL DEFAULT 'post',      -- post|reel
  why         text NOT NULL DEFAULT '',
  evidence    jsonb NOT NULL DEFAULT '[]',
  status      text NOT NULL DEFAULT 'suggested', -- suggested|accepted|dismissed
  action_ref  uuid,                              -- action/production it became
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS content_suggestions_tenant_idx
  ON content_suggestions (tenant_id, status, created_at DESC);
ALTER TABLE content_suggestions ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_suggestions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS content_suggestions_tenant ON content_suggestions;
CREATE POLICY content_suggestions_tenant ON content_suggestions USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);
