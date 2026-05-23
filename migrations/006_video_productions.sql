-- Durable, multi-stage video productions. A production turns an approved
-- script into a finished clip through a state machine that survives restarts:
--
--   queued → planning (script → scene plan)
--          → rendering_clips (per scene: HeyGen avatar | James clip | Runway B-roll)
--          → assembling (Creatomate/Shotstack stitches clips + captions)
--          → succeeded → lands in the approval queue (a human approves)
--          → failed (any stage, recorded honestly)
--
-- Provider-abstracted with stubs so the whole pipeline is provable without
-- spending render credits and never emits a fake mp4.

CREATE TABLE IF NOT EXISTS video_productions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id)
                   DEFAULT current_setting('app.current_tenant', true)::uuid,
  status           text NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued','planning','rendering_clips',
                                     'assembling','succeeded','failed')),
  title            text NOT NULL DEFAULT '',
  platform         text NOT NULL DEFAULT 'instagram',
  aspect           text NOT NULL DEFAULT '9:16',
  script           text NOT NULL DEFAULT '',
  plan             jsonb NOT NULL DEFAULT '{}'::jsonb,
  scenes           jsonb NOT NULL DEFAULT '[]'::jsonb,
  final_url        text,
  error            text,
  avatar_provider  text NOT NULL DEFAULT 'stub',
  broll_provider   text NOT NULL DEFAULT 'stub',
  assembly_provider text NOT NULL DEFAULT 'stub',
  queued_action_id uuid REFERENCES actions(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  completed_at     timestamptz
);
CREATE INDEX IF NOT EXISTS video_productions_tenant_status_idx
  ON video_productions (tenant_id, status, created_at DESC);
ALTER TABLE video_productions ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  EXECUTE 'DROP POLICY IF EXISTS video_productions_tenant ON video_productions';
  EXECUTE 'CREATE POLICY video_productions_tenant ON video_productions USING '
        || '(tenant_id = current_setting(''app.current_tenant'', true)::uuid)';
END $$;
