-- Feedback → change roadmap. Each interpreted feedback item becomes a row:
-- live_config items are applied instantly (status='applied'); code_change
-- items are queued (status='queued') for a coding session and only a human
-- flips them to 'done'. The "What's changing next" board reads this table.
CREATE TABLE IF NOT EXISTS feedback_changes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id)
            DEFAULT current_setting('app.current_tenant', true)::uuid,
  source_event_id uuid,
  production_id uuid,
  area text NOT NULL DEFAULT 'general',         -- broll|captions|music|pacing|voice|layout|text|general
  diagnosis text NOT NULL DEFAULT '',           -- what was disliked
  plain_english text NOT NULL,                  -- user-facing: what's changing/queued
  kind text NOT NULL CHECK (kind IN ('live_config','code_change')),
  config_key text,                              -- set when kind=live_config
  config_value jsonb,                           -- set when kind=live_config
  confidence real NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'queued'
         CHECK (status IN ('applied','queued','dismissed','done')),
  dedupe_key text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  applied_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feedback_changes_tenant_idx ON feedback_changes (tenant_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS feedback_changes_dedupe ON feedback_changes (tenant_id, dedupe_key);
ALTER TABLE feedback_changes ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  EXECUTE 'DROP POLICY IF EXISTS feedback_changes_tenant ON feedback_changes';
  EXECUTE 'CREATE POLICY feedback_changes_tenant ON feedback_changes USING '
        || '(tenant_id = current_setting(''app.current_tenant'', true)::uuid)';
END $$;
