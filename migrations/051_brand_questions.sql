-- Agentic intake: the deep-interview ledger. The Interviewer agent asks;
-- the Researcher agent answers what the open web + the corpus can answer;
-- the human confirms/answers the rest. Every confirmed answer becomes
-- brand memory. "It should be able to ask ten thousand or more questions
-- regarding the brand" — this table is that interview, run continuously.
CREATE TABLE IF NOT EXISTS brand_questions (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id)
               DEFAULT current_setting('app.current_tenant', true)::uuid,
  dimension    text NOT NULL DEFAULT 'identity',
               -- identity|story|audience|offerings|proof|voice|style|
               -- competitors|pov|operations
  question     text NOT NULL,
  answer       text,
  source       text NOT NULL DEFAULT 'open',   -- open|user|research
  confidence   real NOT NULL DEFAULT 0,
  sources      jsonb NOT NULL DEFAULT '[]',    -- research citations
  status       text NOT NULL DEFAULT 'open',   -- open|answered|confirmed|dismissed
  created_at   timestamptz NOT NULL DEFAULT now(),
  answered_at  timestamptz
);
CREATE INDEX IF NOT EXISTS brand_questions_tenant_idx
  ON brand_questions (tenant_id, status, created_at DESC);

ALTER TABLE brand_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE brand_questions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS brand_questions_tenant ON brand_questions;
CREATE POLICY brand_questions_tenant ON brand_questions USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);
