-- Autopilot: durable log of automatic daily content runs. Each run records
-- the ideas it invented, how many drafts it generated, and how many landed
-- in the approval queue. Config itself lives in tenants.config -> 'autopilot'
-- (same pattern as tenant credentials). Nothing here publishes — every
-- generated piece still waits for a human in the queue.

CREATE TABLE IF NOT EXISTS autopilot_runs (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id)
              DEFAULT current_setting('app.current_tenant', true)::uuid,
  status      text NOT NULL DEFAULT 'running'
              CHECK (status IN ('running','succeeded','failed')),
  trigger     text NOT NULL DEFAULT 'manual'
              CHECK (trigger IN ('manual','scheduled')),
  requested   int NOT NULL DEFAULT 0,
  generated   int NOT NULL DEFAULT 0,
  queued      int NOT NULL DEFAULT 0,
  ideas       jsonb NOT NULL DEFAULT '[]'::jsonb,
  results     jsonb NOT NULL DEFAULT '[]'::jsonb,
  error       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS autopilot_runs_tenant_idx
  ON autopilot_runs (tenant_id, created_at DESC);
ALTER TABLE autopilot_runs ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  EXECUTE 'DROP POLICY IF EXISTS autopilot_runs_tenant ON autopilot_runs';
  EXECUTE 'CREATE POLICY autopilot_runs_tenant ON autopilot_runs USING '
        || '(tenant_id = current_setting(''app.current_tenant'', true)::uuid)';
END $$;
