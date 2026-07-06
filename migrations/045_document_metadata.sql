-- Knowledge Base document registry (intelligence parity, Phase 1).
-- One row per uploaded company document: the file's identity, storage
-- location, extraction/indexing status, and (from Phase 3 on) its
-- classification metadata. Chunks + embeddings live in `events`
-- (event_type='document'); this table is the per-FILE ledger the
-- Knowledge Base UI lists and the white-paper corpus assembler reads.
CREATE TABLE IF NOT EXISTS document_metadata (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id)
                  DEFAULT current_setting('app.current_tenant', true)::uuid,
  filename        text NOT NULL,              -- stored (possibly version-bumped) name
  original_name   text NOT NULL DEFAULT '',   -- name as uploaded
  category        text NOT NULL DEFAULT 'company_doc',
  -- Classification metadata (populated by Phase 3 auto-classify; defaults now)
  business_unit   text,
  asset_class     text,
  doc_type        text,
  descriptor      text,
  file_date       date,
  version         int  NOT NULL DEFAULT 1,
  status          text NOT NULL DEFAULT 'Current',
  sensitivity     text NOT NULL DEFAULT 'Internal',
  flagged_for_review boolean NOT NULL DEFAULT false,
  review_reason   text,
  -- Storage + provenance
  source_type     text NOT NULL DEFAULT 'file',
  storage_path    text,                       -- internal supabase://bucket/path
  mime_type       text,
  size_bytes      bigint NOT NULL DEFAULT 0,
  file_hash       text NOT NULL DEFAULT '',   -- links to events dedupe keys
  notes           text,
  -- Extraction + indexing outcome
  extracted_text  text,
  chunks          int  NOT NULL DEFAULT 0,
  indexing_status text NOT NULL DEFAULT 'pending',  -- indexed | skipped | failed | pending
  indexing_error  text,
  indexed_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS document_metadata_tenant_idx
  ON document_metadata (tenant_id, created_at DESC);
-- Collision-safe versioning walks filename v1→v2→… per tenant.
CREATE UNIQUE INDEX IF NOT EXISTS document_metadata_tenant_filename_idx
  ON document_metadata (tenant_id, filename);

ALTER TABLE document_metadata ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_metadata FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS document_metadata_tenant ON document_metadata;
CREATE POLICY document_metadata_tenant ON document_metadata USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);
