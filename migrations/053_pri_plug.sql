-- PRI plug: pull PreReal Intelligence (per silo) into this brand's memory.
-- `pri_pull_log` makes re-pulls idempotent — one row per pulled item, keyed by
-- the PRI id, carrying a content hash so an unchanged item is skipped and a
-- changed one is re-ingested. Tenant-scoped like everything else (RLS).
--
-- The target silos (e.g. spaceport / turtleback) are created on demand at pull
-- time by pri_plug_ingest._ensure_silo — no seed here, so this migration is
-- pure DDL and safe to run through migrate.py against any tenant.

CREATE TABLE IF NOT EXISTS pri_pull_log (
  tenant_id    uuid NOT NULL REFERENCES tenants(id)
               DEFAULT current_setting('app.current_tenant', true)::uuid,
  silo_id      text NOT NULL,          -- BM silo == PRI silo slug (e.g. 'spaceport')
  pri_id       text NOT NULL,          -- PRI file id, or synthetic '<silo>::brief' / '::insights'
  kind         text NOT NULL,          -- doc | brief | insights
  bm_doc_id    uuid,                   -- the document_metadata row we created
  content_hash text NOT NULL,          -- sha256 of the ingested text (skip when unchanged)
  pulled_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, silo_id, pri_id)
);
CREATE INDEX IF NOT EXISTS pri_pull_log_silo_idx ON pri_pull_log (tenant_id, silo_id);

ALTER TABLE pri_pull_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE pri_pull_log FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pri_pull_log_tenant ON pri_pull_log;
CREATE POLICY pri_pull_log_tenant ON pri_pull_log USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- Grant the app's runtime role DML on the new table (Supabase uses a limited
-- `james_app` role, not postgres). Guarded so this is a no-op on a local
-- install where that role doesn't exist.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'james_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON pri_pull_log TO james_app;
  END IF;
END $$;
