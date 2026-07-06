-- Intelligence parity P3: entity registry + silos (per-tenant governance).
-- EntityID format: [BU]-[TypeCode]-[NNN..] (e.g. TBM-H-001). Silos are
-- project/topic corpus groupings (slug ids). Both tenant-scoped; ids are
-- unique PER TENANT (composite PK) so two brands can both own "TBM-H-001".
CREATE TABLE IF NOT EXISTS entities (
  tenant_id        uuid NOT NULL REFERENCES tenants(id)
                   DEFAULT current_setting('app.current_tenant', true)::uuid,
  id               text NOT NULL,             -- the EntityID
  business_unit    text NOT NULL,
  entity_type_code text NOT NULL DEFAULT 'D',
  display_name     text NOT NULL,
  notes            text,
  archived_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS entities_tenant_name_idx
  ON entities (tenant_id, business_unit, lower(display_name));

CREATE TABLE IF NOT EXISTS silos (
  tenant_id   uuid NOT NULL REFERENCES tenants(id)
              DEFAULT current_setting('app.current_tenant', true)::uuid,
  id          text NOT NULL,                  -- slug
  name        text NOT NULL,
  description text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id)
);

-- Documents gain their governance links (nullable — filled by auto-classify).
ALTER TABLE document_metadata ADD COLUMN IF NOT EXISTS entity_id text;
ALTER TABLE document_metadata ADD COLUMN IF NOT EXISTS silo_id text;
CREATE INDEX IF NOT EXISTS document_metadata_silo_idx
  ON document_metadata (tenant_id, silo_id);
CREATE INDEX IF NOT EXISTS document_metadata_entity_idx
  ON document_metadata (tenant_id, entity_id);

ALTER TABLE entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE entities FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS entities_tenant ON entities;
CREATE POLICY entities_tenant ON entities USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);

ALTER TABLE silos ENABLE ROW LEVEL SECURITY;
ALTER TABLE silos FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS silos_tenant ON silos;
CREATE POLICY silos_tenant ON silos USING
  (tenant_id = current_setting('app.current_tenant', true)::uuid);
