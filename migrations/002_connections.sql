-- Social connections per tenant. Stores handle + enable + status.
-- Real OAuth flows are a separate per-platform build; this is config.

CREATE TABLE IF NOT EXISTS connections (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id)
              DEFAULT current_setting('app.current_tenant', true)::uuid,
  platform    text NOT NULL,
  handle      text,
  enabled     boolean NOT NULL DEFAULT false,
  status      text NOT NULL DEFAULT 'not_connected',
  config      jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, platform)
);
CREATE INDEX IF NOT EXISTS connections_tenant_idx ON connections (tenant_id);

ALTER TABLE connections ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  EXECUTE 'DROP POLICY IF EXISTS connections_tenant ON connections';
  EXECUTE 'CREATE POLICY connections_tenant ON connections USING '
        || '(tenant_id = current_setting(''app.current_tenant'', true)::uuid)';
END $$;
