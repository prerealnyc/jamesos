-- Durable video render jobs. Runway-style renders take minutes and are
-- async, so a job must survive a process restart: we persist it, store
-- the provider's job id, and poll for completion. On success the clip is
-- routed into the existing approval queue (actions) as a pending video —
-- nothing publishes without a human.

CREATE TABLE IF NOT EXISTS video_jobs (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id)
                   DEFAULT current_setting('app.current_tenant', true)::uuid,
  provider         text NOT NULL,
  prompt           text NOT NULL,
  prompt_image     text,
  status           text NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued','submitted','processing',
                                     'succeeded','failed')),
  provider_job_id  text,
  result_url       text,
  error            text,
  source_action_id uuid REFERENCES actions(id),
  queued_action_id uuid REFERENCES actions(id),
  payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  completed_at     timestamptz
);
CREATE INDEX IF NOT EXISTS video_jobs_tenant_status_idx
  ON video_jobs (tenant_id, status, created_at DESC);

ALTER TABLE video_jobs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  EXECUTE 'DROP POLICY IF EXISTS video_jobs_tenant ON video_jobs';
  EXECUTE 'CREATE POLICY video_jobs_tenant ON video_jobs USING '
        || '(tenant_id = current_setting(''app.current_tenant'', true)::uuid)';
END $$;
