-- M2: the strategy engine — "it has to think."
-- Three data products per tenant: platform algorithm playbooks (versioned,
-- change-detected), peer benchmarking snapshots, and the weekly
-- Prescription (a quantified plan with per-line evidence).

CREATE TABLE IF NOT EXISTS platform_playbooks (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id)
               DEFAULT current_setting('app.current_tenant', true)::uuid,
  platform     text NOT NULL,
  version      int  NOT NULL DEFAULT 1,
  brief_md     text NOT NULL DEFAULT '',
  key_points   jsonb NOT NULL DEFAULT '[]',   -- structured, diffable
  sources      jsonb NOT NULL DEFAULT '[]',
  changed      boolean NOT NULL DEFAULT false, -- vs previous version
  refreshed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS platform_playbooks_tenant_idx
  ON platform_playbooks (tenant_id, platform, version DESC);
ALTER TABLE platform_playbooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_playbooks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS platform_playbooks_tenant ON platform_playbooks;
CREATE POLICY platform_playbooks_tenant ON platform_playbooks USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);

CREATE TABLE IF NOT EXISTS peer_snapshots (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id)
               DEFAULT current_setting('app.current_tenant', true)::uuid,
  peer         text NOT NULL,
  platform     text NOT NULL DEFAULT '',
  stats        jsonb NOT NULL DEFAULT '{}',   -- cadence/formats/topics/notes
  sources      jsonb NOT NULL DEFAULT '[]',
  captured_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS peer_snapshots_tenant_idx
  ON peer_snapshots (tenant_id, captured_at DESC);
ALTER TABLE peer_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE peer_snapshots FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS peer_snapshots_tenant ON peer_snapshots;
CREATE POLICY peer_snapshots_tenant ON peer_snapshots USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);

CREATE TABLE IF NOT EXISTS prescriptions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id)
                  DEFAULT current_setting('app.current_tenant', true)::uuid,
  week_of         date NOT NULL,
  status          text NOT NULL DEFAULT 'proposed',
                  -- proposed|accepted|partial|expired
  plan            jsonb NOT NULL DEFAULT '[]',
                  -- [{platform, format, per_week, topics[], why, evidence[]}]
  growth_actions  jsonb NOT NULL DEFAULT '[]',
                  -- [{action, why, evidence[]}]
  accepted_items  jsonb NOT NULL DEFAULT '[]',
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS prescriptions_tenant_idx
  ON prescriptions (tenant_id, created_at DESC);
ALTER TABLE prescriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE prescriptions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prescriptions_tenant ON prescriptions;
CREATE POLICY prescriptions_tenant ON prescriptions USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);
