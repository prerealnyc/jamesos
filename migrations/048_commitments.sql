-- Intelligence parity P4: commitments — action items mined from meeting
-- transcripts / documents (owner, due, status), tenant-scoped.
CREATE TABLE IF NOT EXISTS commitments (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id)
                 DEFAULT current_setting('app.current_tenant', true)::uuid,
  text           text NOT NULL,
  owner          text NOT NULL DEFAULT 'unassigned',
  author         text NOT NULL DEFAULT 'transcript',
  entity_id      text,
  source_file_id uuid,                 -- document_metadata row it was mined from
  due            date,
  status         text NOT NULL DEFAULT 'open',   -- open|in_progress|blocked|done|missed
  source_kind    text NOT NULL DEFAULT 'transcript-extraction',
  notes          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS commitments_tenant_idx
  ON commitments (tenant_id, status, created_at DESC);

ALTER TABLE commitments ENABLE ROW LEVEL SECURITY;
ALTER TABLE commitments FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS commitments_tenant ON commitments;
CREATE POLICY commitments_tenant ON commitments USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);
