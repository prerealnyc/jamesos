-- Reference / media library. Holds video assets that drive the video
-- pipeline: style references (examples to replicate), James's real
-- talking-head clips (insertable footage), and B-roll. Files can be
-- uploaded (stored by the app's media storage) or linked by URL.
-- Style `notes` capture, in plain language, what to replicate from a
-- reference; the scene-plan generator reads them as guidance.

CREATE TABLE IF NOT EXISTS media_assets (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id)
               DEFAULT current_setting('app.current_tenant', true)::uuid,
  role         text NOT NULL
               CHECK (role IN ('style_reference','james_clip','broll')),
  title        text NOT NULL DEFAULT '',
  platform     text NOT NULL DEFAULT '',
  source_type  text NOT NULL CHECK (source_type IN ('upload','url')),
  uri          text NOT NULL DEFAULT '',   -- served path (upload) or external URL
  file_path    text,                       -- local storage path for uploads
  mime         text NOT NULL DEFAULT '',
  duration     int  NOT NULL DEFAULT 0,    -- seconds, when known
  tags         text[] NOT NULL DEFAULT '{}',
  notes        text NOT NULL DEFAULT '',   -- style notes: what to replicate
  transcript   text NOT NULL DEFAULT '',
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS media_assets_tenant_role_idx
  ON media_assets (tenant_id, role, created_at DESC);

ALTER TABLE media_assets ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  EXECUTE 'DROP POLICY IF EXISTS media_assets_tenant ON media_assets';
  EXECUTE 'CREATE POLICY media_assets_tenant ON media_assets USING '
        || '(tenant_id = current_setting(''app.current_tenant'', true)::uuid)';
END $$;
