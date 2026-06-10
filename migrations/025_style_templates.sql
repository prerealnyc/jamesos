-- Style Template Library — the "trending video styles" catalog.
--
-- When a style_reference video is uploaded, the Design Inspector watches the
-- WHOLE clip and reverse-engineers a reusable PRODUCTION TEMPLATE: a beat-by-
-- beat map of where every element sits (speaker, captions, on-screen text,
-- logo), how it's paced, what the sound is doing, and which JAMES OS
-- production mode + caption preset would reproduce it. Each inspection is
-- stored here as a NAMED template so the brand can build a library of
-- trending styles and (Phase 2) replicate them on demand.
--
-- The template JSON speaks the assembly engine's own vocabulary (logo
-- positions, caption presets, music moods, production modes) so a stored
-- template can later drive a real render with no translation layer.

CREATE TABLE IF NOT EXISTS style_templates (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id)
                      DEFAULT current_setting('app.current_tenant', true)::uuid,
  -- the reference video this style was extracted from (one template per ref);
  -- SET NULL (not CASCADE) so deleting the source keeps the learned style.
  reference_media_id  uuid REFERENCES media_assets(id) ON DELETE SET NULL,
  name                text NOT NULL DEFAULT 'Untitled style',  -- auto-named from the style
  slug                text NOT NULL DEFAULT '',
  summary             text NOT NULL DEFAULT '',                -- one-line description
  format_type         text NOT NULL DEFAULT '',                -- talking_head | b_roll_montage | ...
  production_mode     text NOT NULL DEFAULT '',                -- JAMES OS mode that best reproduces it
  duration            int  NOT NULL DEFAULT 0,                 -- seconds
  template            jsonb NOT NULL DEFAULT '{}'::jsonb,      -- full structured design spec
  transcript          text NOT NULL DEFAULT '',
  status              text NOT NULL DEFAULT 'ready'
                        CHECK (status IN ('pending','ready','failed')),
  tags                text[] NOT NULL DEFAULT '{}',
  trending_score      real NOT NULL DEFAULT 0,                 -- manual/derived ranking signal
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS style_templates_tenant_created_idx
  ON style_templates (tenant_id, created_at DESC);

-- one live template per reference video (re-inspection updates in place)
CREATE UNIQUE INDEX IF NOT EXISTS style_templates_ref_uniq
  ON style_templates (reference_media_id)
  WHERE reference_media_id IS NOT NULL;

ALTER TABLE style_templates ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  EXECUTE 'DROP POLICY IF EXISTS style_templates_tenant ON style_templates';
  EXECUTE 'CREATE POLICY style_templates_tenant ON style_templates USING '
        || '(tenant_id = current_setting(''app.current_tenant'', true)::uuid)';
END $$;
