-- PRI plug: pull PreReal Intelligence (per silo) into this brand's memory.
-- `pri_pull_log` makes re-pulls idempotent — one row per pulled item, keyed by
-- the PRI id, carrying a content hash so an unchanged item is skipped and a
-- changed one is re-ingested. Tenant-scoped like everything else (RLS).
--
-- Spaceport + Turtleback are seeded as silos for Tenant Zero so pulled
-- intelligence files under the same slug the PRI plug uses (1:1 silo mapping).

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

-- Seed the two BM-bound silos for Tenant Zero (idempotent).
INSERT INTO silos (tenant_id, id, name, description)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'spaceport',  'Spaceport America',
   'PreReal Intelligence for Spaceport, pulled via the PRI plug.'),
  ('00000000-0000-0000-0000-000000000001', 'turtleback', 'Turtleback',
   'PreReal Intelligence for Turtleback, pulled via the PRI plug.')
ON CONFLICT (tenant_id, id) DO NOTHING;
