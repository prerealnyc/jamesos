-- Content Library topic suggestions: the clipper's own "what should we
-- build next" list. Each topic groups 1-4 reel candidates (possibly from
-- different footages) into one buildable edit.
CREATE TABLE IF NOT EXISTS clip_topics (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id)
                DEFAULT current_setting('app.current_tenant', true)::uuid,
  title         text NOT NULL,
  hook          text NOT NULL DEFAULT '',
  why           text NOT NULL DEFAULT '',      -- one-line "why this will perform"
  score         int  NOT NULL DEFAULT 0,       -- 1-10 expected engagement
  segments      jsonb NOT NULL DEFAULT '[]',   -- [{candidate_id, source_id, start_s, end_s, quote, source_title}]
  status        text NOT NULL DEFAULT 'suggested',  -- suggested|dismissed
  production_id uuid,                          -- set once Build kicks a render
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS clip_topics_tenant_idx
  ON clip_topics (tenant_id, status, score DESC);

ALTER TABLE clip_topics ENABLE ROW LEVEL SECURITY;
ALTER TABLE clip_topics FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS clip_topics_tenant ON clip_topics;
CREATE POLICY clip_topics_tenant ON clip_topics USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);
