-- Speaker directory: reusable on-screen name-tags (@handle + subtitle/title).
-- A long-form render can place each speaker's tag as a lower-third for ~2.5s
-- when they first appear, so the audience knows who is who.
CREATE TABLE IF NOT EXISTS speakers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  handle text NOT NULL,                 -- e.g. '@j_prendamano'
  subtitle text NOT NULL DEFAULT '',    -- e.g. 'CEO at PreReal Estate'
  face_ref text NOT NULL DEFAULT '',    -- optional stored face crop / photo url
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS speakers_tenant_idx ON speakers (tenant_id, created_at DESC);

-- Same tenant-scoped RLS as every other app table (media_assets/video_productions).
ALTER TABLE speakers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS speakers_tenant ON speakers;
CREATE POLICY speakers_tenant ON speakers FOR ALL
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
